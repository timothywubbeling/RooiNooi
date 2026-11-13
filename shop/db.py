"""Storage: SQLite locally, Supabase Postgres in the cloud. The code writes SQLite-style SQL
(`?` placeholders, group_concat) and PgConnection translates it for Postgres.

categories     - the groups in the shop menu (Customised crackers, Premade boxes, Napkins)
products       - what you sell, with price, description, minimum per order and SEO text
product_images - the photos of a product, first one is the main photo
option_groups  - choices a customer makes for a product (Outside paper, Filling, Name ...)
option_values  - the answers in a "pick one" group, each with its own extra price / photo
orders         - one row per order, with order_items holding a snapshot of what was bought
media          - the photo files themselves (so nothing depends on a server's disk)
settings       - small key/value store for everything the admin can change
"""
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id               {pk},
    name             TEXT    NOT NULL,
    slug             TEXT    NOT NULL UNIQUE,
    blurb            TEXT,
    meta_title       TEXT,
    meta_description TEXT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id               {pk},
    category_id      INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    name             TEXT    NOT NULL,
    slug             TEXT    NOT NULL UNIQUE,
    sku              TEXT,
    summary          TEXT,
    description      TEXT,
    includes         TEXT,
    price_cents      INTEGER NOT NULL CHECK (price_cents >= 0),
    min_qty          INTEGER NOT NULL DEFAULT 1 CHECK (min_qty >= 1),
    status           TEXT    NOT NULL DEFAULT 'live',
    featured         INTEGER NOT NULL DEFAULT 0,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    meta_title       TEXT,
    meta_description TEXT,
    tags             TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS products_category ON products(category_id);

CREATE TABLE IF NOT EXISTS product_images (
    id         {pk},
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    file       TEXT    NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS product_images_product ON product_images(product_id, sort_order);

CREATE TABLE IF NOT EXISTS option_groups (
    id          {pk},
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'choice',
    required    INTEGER NOT NULL DEFAULT 1,
    help        TEXT,
    price_cents INTEGER NOT NULL DEFAULT 0,
    max_length  INTEGER NOT NULL DEFAULT 40,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS option_groups_product ON option_groups(product_id, sort_order);

CREATE TABLE IF NOT EXISTS option_values (
    id          {pk},
    group_id    INTEGER NOT NULL REFERENCES option_groups(id) ON DELETE CASCADE,
    label       TEXT    NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    image_id    INTEGER REFERENCES product_images(id) ON DELETE SET NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS option_values_group ON option_values(group_id, sort_order);

CREATE TABLE IF NOT EXISTS orders (
    id             TEXT PRIMARY KEY,
    token          TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'new',
    payment_status TEXT    NOT NULL DEFAULT 'unpaid',
    payment_method TEXT,
    checkout_id    TEXT,
    customer_name  TEXT    NOT NULL,
    email          TEXT,
    phone          TEXT    NOT NULL,
    fulfilment     TEXT    NOT NULL,
    address        TEXT,
    customer_note  TEXT,
    admin_note     TEXT,
    subtotal_cents INTEGER NOT NULL,
    delivery_cents INTEGER NOT NULL DEFAULT 0,
    delivery_quote INTEGER NOT NULL DEFAULT 0,
    total_cents    INTEGER NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    paid_at        TEXT
);
CREATE INDEX IF NOT EXISTS orders_status_created ON orders(status, created_at);
CREATE INDEX IF NOT EXISTS orders_created ON orders(created_at);

CREATE TABLE IF NOT EXISTS order_items (
    id               {pk},
    order_id         TEXT    NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name     TEXT    NOT NULL,
    sku              TEXT,
    image_file       TEXT,
    options_json     TEXT    NOT NULL DEFAULT '[]',
    unit_price_cents INTEGER NOT NULL,
    qty              INTEGER NOT NULL CHECK (qty > 0),
    line_total_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS order_items_order ON order_items(order_id);

CREATE TABLE IF NOT EXISTS media (
    name         TEXT PRIMARY KEY,
    content_type TEXT    NOT NULL,
    size         INTEGER NOT NULL,
    data         {blob}  NOT NULL,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

TABLES = ("categories", "products", "product_images", "option_groups", "option_values",
          "orders", "order_items", "media", "settings")
INT_ID_TABLES = {"categories", "products", "product_images", "option_groups", "option_values", "order_items"}

SETTING_DEFAULTS = {
    "store_name": "Rooi Nooi",
    "tagline": "Elke partytjie kort 'n Rooi Nooi",
    "store_open": "1",
    "announcement": "Christmas orders are open · Build your own crackers",
    "phone": "068 327 6896",
    "whatsapp": "27683276896",
    "email": "rooinooievents@gmail.com",
    "address": "882 Patryshond Street, Garsfontein X10, Pretoria, 0060",
    "instagram": "",
    "facebook": "",
    "about": ("Rooi Nooi is all about the little extras that make every occasion memorable. "
              "From thoughtful gifts and party essentials to fun surprises and everything in between, "
              "we've got just the thing to bring a little more personality to your celebrations."),
    "meta_description": ("Customised Christmas crackers, premade cracker boxes and festive napkins "
                         "from Rooi Nooi Events in Pretoria."),
    "hero_kicker": "Shop our",
    "hero_title": "Christmas",
    "hero_script": "Crackers",
    "hero_left": "",
    "hero_right": "",
    "collect_enabled": "1",
    "collect_note": "Collect from us in Garsfontein, Pretoria. We'll let you know when your order is ready.",
    "delivery_enabled": "1",
    "delivery_fee": "",
    "delivery_note": "We deliver in Pretoria. We'll confirm the delivery cost with you.",
    "order_message": ("Thank you for your order! Online card payments are coming soon, so for now we'll "
                      "contact you on WhatsApp or email to confirm your order and arrange payment."),
}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Postgres translation ────────────────────────────────────────────────────
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)
_sql_cache = {}


def _pg_sql(sql):
    """-> (postgres sql, whether ' RETURNING id' was added so lastrowid works)."""
    out = _sql_cache.get(sql)
    if out is None:
        text = sql.replace("%", "%%").replace("?", "%s").replace("group_concat(", "string_agg(")
        m = _INSERT_RE.match(text)
        returning = bool(m and m.group(1).lower() in INT_ID_TABLES and "RETURNING" not in text.upper())
        out = (text + " RETURNING id" if returning else text, returning)
        _sql_cache[sql] = out
    return out


class Row(dict):
    """A Postgres row usable like sqlite3.Row: row["name"] and row[0] both work."""

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return tuple(self.values())[key]
        return dict.__getitem__(self, key)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []

    def make(values):
        return Row(zip(names, values))
    return make


class PgResult:
    """Looks enough like a sqlite3 cursor: fetchone/fetchall/iterate, lastrowid, rowcount."""

    def __init__(self, cursor, returning_id):
        self._cur = cursor
        self.lastrowid = None
        self.rowcount = cursor.rowcount
        self._buffer = None
        if returning_id:
            row = cursor.fetchone()
            self.lastrowid = row["id"] if row else None

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConnection:
    """A long-lived Postgres connection used like sqlite3: `execute` and `with conn:`."""

    def __init__(self, raw):
        self.raw = raw
        self._tx = []
        self.last_used = time.time()

    def execute(self, sql, params=()):
        pg, returning = _pg_sql(sql)
        cur = self.raw.cursor()
        cur.execute(pg, tuple(params) if params else None)
        self.last_used = time.time()
        return PgResult(cur, returning)

    def __enter__(self):
        tx = self.raw.transaction()
        tx.__enter__()
        self._tx.append(tx)
        return self

    def __exit__(self, exc_type, exc, tb):
        tx = self._tx.pop()
        return tx.__exit__(exc_type, exc, tb)

    def close(self):
        """Kept open for the next request (connecting to Supabase takes a while)."""


class Database:
    def __init__(self, path=None, url=None):
        self.url = url or ""
        self.kind = "pg" if self.url else "sqlite"
        self.path = str(path) if path else None
        self._init_lock = threading.Lock()
        self._local = threading.local()

    # ── connections ─────────────────────────────────────────────────────
    def connect(self):
        if self.kind == "sqlite":
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            return conn
        conn = getattr(self._local, "conn", None)
        if conn is not None and (conn.raw.closed or conn.raw.broken):
            conn = None
        if conn is not None and time.time() - conn.last_used > 30:
            try:                                   # the pooler may have dropped an idle connection
                conn.raw.execute("SELECT 1")
            except Exception:
                conn = None
        if conn is None:
            conn = PgConnection(self._pg_connect())
            self._local.conn = conn
        return conn

    def _pg_connect(self):
        import psycopg
        # prepare_threshold=None: Supabase's transaction pooler cannot keep prepared statements.
        return psycopg.connect(self.url, autocommit=True, row_factory=_row_factory,
                               prepare_threshold=None, connect_timeout=15)

    def lock(self, conn):
        """Stop two servers starting at the same moment from both setting up the database."""
        if self.kind == "pg":
            conn.execute("SELECT pg_advisory_xact_lock(7263510)")

    # ── schema ──────────────────────────────────────────────────────────
    def init(self):
        with self._init_lock:
            conn = self.connect()
            try:
                if self.kind == "sqlite":
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.executescript(SCHEMA.format(pk="INTEGER PRIMARY KEY", blob="BLOB") + ";")
                    with conn:
                        self._defaults(conn)
                        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    return
                ddl = SCHEMA.format(pk="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", blob="BYTEA")
                with conn:
                    self.lock(conn)
                    for statement in ddl.split(";"):
                        if statement.strip():
                            conn.raw.execute(statement)
                    # Supabase publishes the public schema through its Data API. Lock these tables
                    # so only this app (the table owner) can read them, never the anon/public keys.
                    for table in TABLES:
                        conn.raw.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                    conn.raw.execute("""
                        DO $$ BEGIN
                          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                            EXECUTE 'REVOKE ALL ON """ + ", ".join(TABLES) + """ FROM anon, authenticated';
                          END IF;
                        END $$""")
                    self._defaults(conn)
            finally:
                conn.close()

    def _defaults(self, conn):
        for key, value in SETTING_DEFAULTS.items():
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (key, value))


# ── Settings ────────────────────────────────────────────────────────────────
def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return SETTING_DEFAULTS.get(key, default)
    return row["value"]


def all_settings(conn):
    values = dict(SETTING_DEFAULTS)
    values.update({row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")})
    return values


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
