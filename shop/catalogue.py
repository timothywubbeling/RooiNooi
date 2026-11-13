"""Products, their photos and options, and working out what a bag costs.

Prices are always worked out here on the server from the database, so a customer's
browser can never change what they pay.
"""
import json
import re

from .db import utcnow

STATUSES = {"live": "In the shop", "soldout": "Sold out", "hidden": "Hidden"}
KINDS = {"choice": "Pick one", "text": "Text (e.g. a name)"}
MAX_LINES = 60
MAX_QTY = 200


# ── slugs ───────────────────────────────────────────────────────────────────
def slugify(text):
    text = str(text or "").lower().replace("ë", "e").replace("é", "e").replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug[:80].strip("-") or "item"


def unique_slug(conn, table, wanted, exclude_id=None):
    base = slugify(wanted)
    slug, n = base, 2
    while True:
        row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
        if row is None or row["id"] == exclude_id:
            return slug
        slug = f"{base}-{n}"
        n += 1


# ── categories ──────────────────────────────────────────────────────────────
def list_categories(conn, public=False):
    sql = """
        SELECT c.*,
               (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id) AS product_count,
               (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id AND p.status != 'hidden') AS live_count,
               (SELECT pi.file FROM products p JOIN product_images pi ON pi.product_id = p.id
                 WHERE p.category_id = c.id AND p.status != 'hidden'
                 ORDER BY p.sort_order, p.id, pi.sort_order, pi.id LIMIT 1) AS image,
               (SELECT p.slug FROM products p WHERE p.category_id = c.id AND p.status != 'hidden'
                 ORDER BY p.sort_order, p.id LIMIT 1) AS first_slug
          FROM categories c
    """
    if public:
        sql += " WHERE c.active = 1"
    sql += " ORDER BY c.sort_order, c.id"
    rows = conn.execute(sql).fetchall()
    if public:
        rows = [r for r in rows if r["live_count"] > 0]
    return rows


def get_category(conn, category_id):
    return conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()


def category_by_slug(conn, slug):
    return conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()


# ── products ────────────────────────────────────────────────────────────────
PRODUCT_SELECT = """
    SELECT p.*, c.name AS category_name, c.slug AS category_slug, c.active AS category_active,
           (SELECT file FROM product_images i WHERE i.product_id = p.id ORDER BY i.sort_order, i.id LIMIT 1) AS image,
           (SELECT COUNT(*) FROM option_groups og WHERE og.product_id = p.id) AS option_count
      FROM products p
      LEFT JOIN categories c ON c.id = p.category_id
"""


def list_products(conn, public=False, category_id=None, featured=False):
    where, args = [], []
    if public:
        where.append("p.status != 'hidden' AND (c.id IS NULL OR c.active = 1)")
    if category_id is not None:
        where.append("p.category_id = ?")
        args.append(category_id)
    if featured:
        where.append("p.featured = 1")
    sql = PRODUCT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(c.sort_order, 999), p.sort_order, lower(p.name)"
    return conn.execute(sql, args).fetchall()


def get_product(conn, product_id):
    return conn.execute(PRODUCT_SELECT + " WHERE p.id = ?", (product_id,)).fetchone()


def product_by_slug(conn, slug):
    return conn.execute(PRODUCT_SELECT + " WHERE p.slug = ?", (slug,)).fetchone()


def is_public(product):
    return product is not None and product["status"] != "hidden" and product["category_active"] in (None, 1)


def product_images(conn, product_id):
    return conn.execute(
        "SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order, id", (product_id,)).fetchall()


def option_groups(conn, product_id, public=False):
    """Option groups with their values, as plain dicts."""
    groups = [dict(r) for r in conn.execute(
        "SELECT * FROM option_groups WHERE product_id = ? ORDER BY sort_order, id", (product_id,))]
    if not groups:
        return []
    ids = [g["id"] for g in groups]
    values = conn.execute(
        f"SELECT v.*, i.file AS image_file FROM option_values v "
        f"LEFT JOIN product_images i ON i.id = v.image_id "
        f"WHERE v.group_id IN ({','.join('?' * len(ids))}) ORDER BY v.sort_order, v.id", ids).fetchall()
    by_group = {}
    for v in values:
        if public and not v["active"]:
            continue
        by_group.setdefault(v["group_id"], []).append(dict(v))
    for g in groups:
        g["values"] = by_group.get(g["id"], [])
        g["has_images"] = any(v["image_file"] for v in g["values"])
        g["has_prices"] = any(v["price_cents"] for v in g["values"])
    return groups


def price_range(product, groups):
    """(lowest price, can the price change with options?) for "From R…" labels."""
    low = product["price_cents"]
    varies = False
    for g in groups:
        if g["kind"] == "text":
            varies = varies or g["price_cents"] > 0
            continue
        prices = [v["price_cents"] for v in g["values"] if v["active"]]
        if not prices:
            continue
        if len(set(prices)) > 1 or (prices[0] and not g["required"]):
            varies = True
        if g["required"]:
            low += min(prices)
    return low, varies


def price_labels(conn, products):
    """{product_id: 'From R18.90' / 'R145'} for a list of products."""
    from .helpers import rands
    labels = {}
    for p in products:
        low, varies = price_range(p, option_groups(conn, p["id"]))
        labels[p["id"]] = ("From " if varies else "") + rands(low)
    return labels


def product_payload(product, groups, images):
    """What the product page script needs to price and add the product."""
    return {
        "id": product["id"],
        "name": product["name"],
        "price_cents": product["price_cents"],
        "min_qty": product["min_qty"],
        "status": product["status"],
        "images": [{"id": i["id"], "file": i["file"]} for i in images],
        "groups": [{
            "id": g["id"], "name": g["name"], "kind": g["kind"], "required": bool(g["required"]),
            "price_cents": g["price_cents"], "max_length": g["max_length"],
            "values": [{"id": v["id"], "label": v["label"], "price_cents": v["price_cents"],
                        "image_id": v["image_id"]} for v in g["values"]],
        } for g in groups],
    }


# ── pricing a bag ───────────────────────────────────────────────────────────
def _load(conn, product_id, cache):
    if product_id not in cache:
        product = get_product(conn, product_id)
        cache[product_id] = {
            "product": product,
            "groups": option_groups(conn, product_id) if product else [],
        }
    return cache[product_id]


def _price_one(conn, raw, cache):
    from .helpers import img
    line = {"key": "", "product_id": None, "name": "Unknown item", "url": None, "image": None,
            "sku": None, "image_file": None, "qty": 1, "unit_cents": 0, "line_cents": 0,
            "options": [], "error": None}
    if not isinstance(raw, dict):
        line["error"] = "This item could not be read. Please remove it."
        return line
    line["key"] = str(raw.get("key") or "")[:300]
    try:
        product_id = int(raw.get("product_id"))
        qty = int(raw.get("qty"))
    except (TypeError, ValueError):
        line["error"] = "This item could not be read. Please remove it."
        return line
    entry = _load(conn, product_id, cache)
    product = entry["product"]
    line["product_id"] = product_id
    line["qty"] = max(1, min(MAX_QTY, qty))
    if not is_public(product):
        line["error"] = "This item is no longer available. Please remove it."
        return line
    line.update(name=product["name"], sku=product["sku"], image_file=product["image"],
                url=f"/product/{product['slug']}")
    if product["status"] == "soldout":
        line["error"] = "Sold out at the moment. Please remove it."
        line["image"] = img(line["image_file"])
        return line

    chosen = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    unit = product["price_cents"]

    def fail(message):
        line["error"] = line["error"] or message      # keep the first problem

    for group in entry["groups"]:
        picked = chosen.get(str(group["id"]))
        if group["kind"] == "text":
            text = " ".join(str(picked or "").split())[:group["max_length"]]
            if not text:
                if group["required"]:
                    fail(f"Please fill in “{group['name']}”.")
                continue
            unit += group["price_cents"]
            line["options"].append({"group": group["name"], "value": text, "price_cents": group["price_cents"]})
            continue
        if picked in (None, "", 0, "0"):
            if group["required"] and any(v["active"] for v in group["values"]):
                fail(f"Please choose “{group['name']}”.")
            continue
        value = next((v for v in group["values"] if str(v["id"]) == str(picked)), None)
        if value is None:
            fail(f"Your “{group['name']}” choice is no longer available. Please remove this item and add it again.")
            continue
        if not value["active"]:
            fail(f"{value['label']} is sold out. Please remove this item and pick something else.")
            continue
        unit += value["price_cents"]
        line["options"].append({"group": group["name"], "value": value["label"], "price_cents": value["price_cents"]})
        if value["image_file"]:
            line["image_file"] = value["image_file"]
    line["unit_cents"] = unit
    line["line_cents"] = unit * line["qty"]
    line["image"] = img(line["image_file"])
    return line


def price_lines(conn, raw_lines):
    """Price a bag. Returns lines, subtotal, problems and whether it can be checked out."""
    if not isinstance(raw_lines, list):
        raw_lines = []
    cache = {}
    lines = [_price_one(conn, raw, cache) for raw in raw_lines[:MAX_LINES]]
    good = [l for l in lines if l["error"] is None]
    qty_by_product = {}
    for l in good:
        qty_by_product[l["product_id"]] = qty_by_product.get(l["product_id"], 0) + l["qty"]
    problems = []
    for product_id, qty in qty_by_product.items():
        product = cache[product_id]["product"]
        if qty < product["min_qty"]:
            short = product["min_qty"] - qty
            problems.append({
                "product_id": product_id,
                "message": f"{product['name']}: minimum of {product['min_qty']} per order. "
                           f"You have {qty}, add {short} more.",
            })
    if any(l["error"] for l in lines):
        problems.append({"product_id": None, "message": "Some items in your bag need attention."})
    return {
        "lines": lines,
        "subtotal_cents": sum(l["line_cents"] for l in good),
        "count": sum(l["qty"] for l in good),
        "problems": problems,
        "ok": bool(good) and not problems,
    }


def order_options(row):
    try:
        return json.loads(row["options_json"] or "[]")
    except (TypeError, ValueError):
        return []


# ── saving options from the admin form ──────────────────────────────────────
class OptionError(ValueError):
    pass


def parse_options(raw_json):
    """Check the options editor's JSON. Returns a clean list or raises OptionError."""
    from .helpers import parse_money
    try:
        data = json.loads(raw_json or "[]")
    except ValueError as exc:
        raise OptionError("The options could not be read. Reload the page and try again.") from exc
    if not isinstance(data, list):
        raise OptionError("The options could not be read.")
    if len(data) > 20:
        raise OptionError("A product can have at most 20 option groups.")
    groups = []
    for gi, g in enumerate(data, 1):
        if not isinstance(g, dict):
            raise OptionError("The options could not be read.")
        name = " ".join(str(g.get("name") or "").split())[:60]
        kind = g.get("kind") if g.get("kind") in KINDS else "choice"
        if not name:
            raise OptionError(f"Option group {gi} needs a name, e.g. “Filling”.")
        price = parse_money(g.get("price"))
        if price is None:
            raise OptionError(f"“{name}”: enter the extra cost like 1.00, or 0.")
        try:
            max_length = max(1, min(200, int(g.get("max_length") or 40)))
        except (TypeError, ValueError):
            max_length = 40
        group = {
            "id": _int_or_none(g.get("id")), "name": name, "kind": kind,
            "required": bool(g.get("required")), "help": " ".join(str(g.get("help") or "").split())[:200],
            "price_cents": price if kind == "text" else 0, "max_length": max_length, "values": [],
        }
        if kind == "choice":
            values = g.get("values") if isinstance(g.get("values"), list) else []
            if not values:
                raise OptionError(f"“{name}” needs at least one choice.")
            if len(values) > 80:
                raise OptionError(f"“{name}” has too many choices (max 80).")
            for v in values:
                if not isinstance(v, dict):
                    raise OptionError("The options could not be read.")
                label = " ".join(str(v.get("label") or "").split())[:80]
                if not label:
                    raise OptionError(f"“{name}” has a choice without a name.")
                vprice = parse_money(v.get("price"))
                if vprice is None:
                    raise OptionError(f"“{name} → {label}”: enter the extra cost like 22.50, or 0.")
                group["values"].append({
                    "id": _int_or_none(v.get("id")), "label": label, "price_cents": vprice,
                    "image": str(v.get("image") or ""), "active": v.get("active", True) is not False,
                })
        groups.append(group)
    return groups


def _int_or_none(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def save_options(conn, product_id, groups, resolve_image):
    """Sync the product's option groups/values with `groups`, keeping ids where possible
    (so bags customers already filled keep working). `resolve_image(ref)` -> image id or None."""
    old_groups = {r["id"] for r in conn.execute("SELECT id FROM option_groups WHERE product_id = ?", (product_id,))}
    keep_groups = set()
    for gi, g in enumerate(groups):
        if g["id"] in old_groups:
            group_id = g["id"]
            conn.execute(
                "UPDATE option_groups SET name=?, kind=?, required=?, help=?, price_cents=?, max_length=?, sort_order=? "
                "WHERE id=?",
                (g["name"], g["kind"], 1 if g["required"] else 0, g["help"] or None, g["price_cents"],
                 g["max_length"], gi, group_id))
        else:
            group_id = conn.execute(
                "INSERT INTO option_groups(product_id, name, kind, required, help, price_cents, max_length, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (product_id, g["name"], g["kind"], 1 if g["required"] else 0, g["help"] or None,
                 g["price_cents"], g["max_length"], gi)).lastrowid
        keep_groups.add(group_id)
        old_values = {r["id"] for r in conn.execute("SELECT id FROM option_values WHERE group_id = ?", (group_id,))}
        keep_values = set()
        for vi, v in enumerate(g["values"]):
            image_id = resolve_image(v["image"]) if v["image"] else None
            if v["id"] in old_values:
                conn.execute(
                    "UPDATE option_values SET label=?, price_cents=?, image_id=?, active=?, sort_order=? WHERE id=?",
                    (v["label"], v["price_cents"], image_id, 1 if v["active"] else 0, vi, v["id"]))
                keep_values.add(v["id"])
            else:
                keep_values.add(conn.execute(
                    "INSERT INTO option_values(group_id, label, price_cents, image_id, active, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (group_id, v["label"], v["price_cents"], image_id, 1 if v["active"] else 0, vi)).lastrowid)
        for gone in old_values - keep_values:
            conn.execute("DELETE FROM option_values WHERE id = ?", (gone,))
    for gone in old_groups - keep_groups:
        conn.execute("DELETE FROM option_groups WHERE id = ?", (gone,))


def options_for_editor(conn, product_id):
    """The options editor's starting data (image refs are 'img:<id>')."""
    out = []
    for g in option_groups(conn, product_id):
        out.append({
            "id": g["id"], "name": g["name"], "kind": g["kind"], "required": bool(g["required"]),
            "help": g["help"] or "", "price": f"{g['price_cents'] / 100:.2f}", "max_length": g["max_length"],
            "values": [{"id": v["id"], "label": v["label"], "price": f"{v['price_cents'] / 100:.2f}",
                        "image": f"img:{v['image_id']}" if v["image_id"] else "",
                        "image_file": v["image_file"] or "", "active": bool(v["active"])}
                       for v in g["values"]],
        })
    return out


def duplicate_product(conn, product_id):
    """Copy a product with its photos and options. The copy starts hidden."""
    p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if p is None:
        return None
    now = utcnow()
    name = f"{p['name']} (copy)"
    new_id = conn.execute(
        "INSERT INTO products(category_id, name, slug, sku, summary, description, includes, price_cents, min_qty, "
        "status, featured, sort_order, meta_title, meta_description, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'hidden', 0, ?, ?, ?, ?, ?, ?)",
        (p["category_id"], name, unique_slug(conn, "products", name), None, p["summary"], p["description"],
         p["includes"], p["price_cents"], p["min_qty"], p["sort_order"] + 1, None, None, p["tags"], now, now),
    ).lastrowid
    image_map = {}
    for im in product_images(conn, product_id):
        image_map[im["id"]] = conn.execute(
            "INSERT INTO product_images(product_id, file, sort_order) VALUES (?, ?, ?)",
            (new_id, im["file"], im["sort_order"])).lastrowid
    for g in conn.execute("SELECT * FROM option_groups WHERE product_id = ? ORDER BY sort_order, id", (product_id,)).fetchall():
        gid = conn.execute(
            "INSERT INTO option_groups(product_id, name, kind, required, help, price_cents, max_length, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id, g["name"], g["kind"], g["required"], g["help"], g["price_cents"], g["max_length"], g["sort_order"]),
        ).lastrowid
        for v in conn.execute("SELECT * FROM option_values WHERE group_id = ? ORDER BY sort_order, id", (g["id"],)).fetchall():
            conn.execute(
                "INSERT INTO option_values(group_id, label, price_cents, image_id, active, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (gid, v["label"], v["price_cents"], image_map.get(v["image_id"]), v["active"], v["sort_order"]))
    return new_id
