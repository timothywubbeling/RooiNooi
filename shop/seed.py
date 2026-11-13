"""Fill an empty shop with the Rooi Nooi starter catalogue (seed/catalogue.json + seed/images).

Runs once: after that the "seeded" setting stays on, so deleting every product in the
admin never brings the starter products back.
"""
import json
from pathlib import Path

from . import db as dbm
from .catalogue import unique_slug
from .helpers import parse_money
from .images import store_bytes


def seed_if_empty(conn, seed_dir):
    seed_dir = Path(seed_dir)
    if dbm.get_setting(conn, "seeded") == "1":
        return False
    if conn.execute("SELECT 1 FROM products LIMIT 1").fetchone():
        dbm.set_setting(conn, "seeded", "1")
        return False
    path = seed_dir / "catalogue.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = {}

    def photo(name):
        if name not in stored:
            stored[name] = store_bytes(conn, (seed_dir / "images" / name).read_bytes())
        return stored[name]

    now = dbm.utcnow()
    for ci, cat in enumerate(data["categories"]):
        cat_id = conn.execute(
            "INSERT INTO categories(name, slug, blurb, meta_title, meta_description, sort_order, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (cat["name"], unique_slug(conn, "categories", cat["slug"]), cat.get("blurb"),
             cat.get("meta_title"), cat.get("meta_description"), ci, now, now),
        ).lastrowid
        box_options = cat.get("box_options")
        for pi, p in enumerate(cat["products"]):
            product_id = conn.execute(
                "INSERT INTO products(category_id, name, slug, sku, summary, description, includes, price_cents, "
                "min_qty, status, featured, sort_order, meta_title, meta_description, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, ?, ?)",
                (cat_id, p["name"], unique_slug(conn, "products", p["slug"]), p.get("sku"), p.get("summary"),
                 p.get("description"), p.get("includes"), parse_money(p["price"]), p.get("min_qty", 1),
                 1 if p.get("featured") else 0, pi, p.get("meta_title"), p.get("meta_description"),
                 p.get("tags"), now, now),
            ).lastrowid

            images = list(p.get("images") or [])
            options = list(p.get("options") or [])
            if box_options:
                # Premade boxes: every box can come in each outside paper; the photo follows the paper.
                files = [o["image"] for o in box_options]
                main = p.get("main_image")
                images = ([main] if main else []) + [f for f in files if f != main]
                options = [{"name": "Outside paper", "kind": "choice", "required": True,
                            "help": "Pick the outside paper you like best.", "values": box_options}]

            image_ids = {}
            for ii, name in enumerate(images):
                image_ids[name] = conn.execute(
                    "INSERT INTO product_images(product_id, file, sort_order) VALUES (?, ?, ?)",
                    (product_id, photo(name), ii)).lastrowid

            for gi, g in enumerate(options):
                group_id = conn.execute(
                    "INSERT INTO option_groups(product_id, name, kind, required, help, price_cents, max_length, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (product_id, g["name"], g.get("kind", "choice"), 1 if g.get("required", True) else 0,
                     g.get("help"), parse_money(g.get("price", "0")), g.get("max_length", 40), gi),
                ).lastrowid
                for vi, v in enumerate(g.get("values") or []):
                    conn.execute(
                        "INSERT INTO option_values(group_id, label, price_cents, image_id, active, sort_order) "
                        "VALUES (?, ?, ?, ?, 1, ?)",
                        (group_id, v["label"], parse_money(v.get("price", "0")), image_ids.get(v.get("image")), vi))

    for key, name in (data.get("settings") or {}).items():
        dbm.set_setting(conn, key, photo(name))
    dbm.set_setting(conn, "seeded", "1")
    print(f">> loaded the starter catalogue from {path}")
    return True
