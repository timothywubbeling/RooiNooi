"""The shop customers see: home, categories, product pages, bag, checkout and order page."""
import json
import re
import secrets

from flask import Blueprint, Response, abort, g, jsonify, render_template, request, url_for

from . import catalogue as cat
from . import db as dbm
from . import images
from .helpers import parse_money, public_base, rands, services

bp = Blueprint("store", __name__)

ORDER_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def fulfilment_options(settings):
    """The ways to get an order, as switched on in Settings."""
    out = []
    if settings.get("collect_enabled") == "1":
        out.append({"id": "collect", "label": "Collect", "note": settings.get("collect_note") or "",
                    "fee_cents": 0, "quote": False})
    if settings.get("delivery_enabled") == "1":
        fee_text = (settings.get("delivery_fee") or "").strip()
        fee = parse_money(fee_text) if fee_text else None
        out.append({"id": "delivery", "label": "Delivery", "note": settings.get("delivery_note") or "",
                    "fee_cents": fee or 0, "quote": fee is None})
    return out


@bp.app_context_processor
def shop_context():
    if getattr(g, "db", None) is None:
        return {}
    settings = dbm.all_settings(g.db)
    menu = []
    for c in cat.list_categories(g.db, public=True):
        # A category with one product goes straight to it.
        href = (url_for("store.product", slug=c["first_slug"]) if c["live_count"] == 1
                else url_for("store.category", slug=c["slug"]))
        menu.append({"name": c["name"], "slug": c["slug"], "href": href})
    return {"S": settings, "menu": menu, "store_open": settings.get("store_open") == "1"}


@bp.get("/")
def home():
    featured = cat.list_products(g.db, public=True, featured=True)
    if not featured:
        featured = cat.list_products(g.db, public=True)[:8]
    categories = cat.list_categories(g.db, public=True)
    builder = next((p for p in cat.list_products(g.db, public=True)
                    if p["min_qty"] > 1 or p["option_count"] >= 3), None)
    labels = cat.price_labels(g.db, featured + ([builder] if builder else []))
    cat_labels = {}
    for c in categories:
        ranges = [cat.price_range(p, cat.option_groups(g.db, p["id"]))
                  for p in cat.list_products(g.db, public=True, category_id=c["id"])]
        if ranges:
            low = min(r[0] for r in ranges)
            varies = any(r[1] for r in ranges) or len({r[0] for r in ranges}) > 1
            cat_labels[c["id"]] = ("From " if varies else "") + rands(low)
    builder_groups = cat.option_groups(g.db, builder["id"], public=True) if builder else []
    return render_template("store/home.html", featured=featured, labels=labels, categories=categories,
                           cat_labels=cat_labels, builder=builder, builder_groups=builder_groups)


@bp.get("/shop")
def shop_all():
    sections = []
    for c in cat.list_categories(g.db, public=True):
        products = cat.list_products(g.db, public=True, category_id=c["id"])
        if products:
            sections.append((c, products))
    loose = [p for p in cat.list_products(g.db, public=True) if p["category_id"] is None]
    if loose:
        sections.append((None, loose))
    labels = cat.price_labels(g.db, [p for _, ps in sections for p in ps])
    return render_template("store/shop.html", sections=sections, labels=labels, current=None)


@bp.get("/shop/<slug>")
def category(slug):
    c = cat.category_by_slug(g.db, slug)
    if c is None or not c["active"]:
        abort(404)
    products = cat.list_products(g.db, public=True, category_id=c["id"])
    labels = cat.price_labels(g.db, products)
    return render_template("store/shop.html", sections=[(c, products)], labels=labels, current=c)


@bp.get("/product/<slug>")
def product(slug):
    p = cat.product_by_slug(g.db, slug)
    if not cat.is_public(p):
        abort(404)
    groups = cat.option_groups(g.db, p["id"], public=True)
    images = cat.product_images(g.db, p["id"])
    low, varies = cat.price_range(p, groups)
    related = [r for r in cat.list_products(g.db, public=True, category_id=p["category_id"]) if r["id"] != p["id"]][:4] \
        if p["category_id"] else []
    return render_template(
        "store/product.html", p=p, groups=groups, images=images, low=low, varies=varies,
        payload=cat.product_payload(p, groups, images), related=related,
        related_labels=cat.price_labels(g.db, related))


@bp.get("/checkout")
def checkout():
    settings = dbm.all_settings(g.db)
    return render_template("store/checkout.html", ways=fulfilment_options(settings))


@bp.get("/order/<order_id>")
def order_page(order_id):
    order = g.db.execute("SELECT * FROM orders WHERE id = ?", (order_id.upper(),)).fetchone()
    token = request.args.get("t", "")
    if order is None or not token or not secrets.compare_digest(token, order["token"]):
        abort(404)
    items = g.db.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order["id"],)).fetchall()
    options = {i["id"]: cat.order_options(i) for i in items}
    return render_template("store/order.html", order=order, items=items, options=options,
                           just_placed=request.args.get("new") == "1")


@bp.get("/media/<name>")
def media(name):
    found = images.load(g.db, name)
    if found is None:
        abort(404)
    data, content_type = found
    resp = Response(data, mimetype=content_type)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # names come from the file contents
    return resp


# ── bag + orders API ────────────────────────────────────────────────────────
def _client():
    return request.remote_addr or "?"


@bp.post("/api/quote")
def api_quote():
    if not services().quote_limiter.allow(_client()):
        return jsonify({"error": "Too many requests. Wait a moment."}), 429
    data = request.get_json(silent=True) or {}
    return jsonify(cat.price_lines(g.db, data.get("lines")))


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


@bp.post("/api/orders")
def api_order():
    if not request.is_json:
        abort(400)
    settings = dbm.all_settings(g.db)
    if settings.get("store_open") != "1":
        return jsonify({"error": "We're not taking orders right now. Please try again later."}), 409
    data = request.get_json(silent=True) or {}
    quote = cat.price_lines(g.db, data.get("lines"))
    if not quote["ok"]:
        message = quote["problems"][0]["message"] if quote["problems"] else "Your bag is empty."
        return jsonify({"error": message, "quote": quote}), 400

    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    name = _clean(customer.get("name"), 80)
    phone = _clean(customer.get("phone"), 30)
    email = _clean(customer.get("email"), 120)
    note = str(customer.get("note") or "").strip()[:600]
    errors = {}
    if len(name) < 2:
        errors["name"] = "Please enter your name."
    if len(re.sub(r"\D", "", phone)) < 9:
        errors["phone"] = "Please enter a phone number we can WhatsApp or call."
    if email and not EMAIL_RE.match(email):
        errors["email"] = "That email address doesn't look right."

    ways = {w["id"]: w for w in fulfilment_options(settings)}
    way = ways.get(str(data.get("fulfilment") or ""))
    if way is None:
        errors["fulfilment"] = "Please choose collection or delivery."
    address = str(customer.get("address") or "").strip()[:400]
    if way and way["id"] == "delivery" and len(address) < 8:
        errors["address"] = "Please enter the delivery address."
    if errors:
        return jsonify({"error": next(iter(errors.values())), "fields": errors}), 400

    if not services().order_limiter.allow(_client()):
        return jsonify({"error": "Too many orders from this device. Please contact us on WhatsApp."}), 429

    delivery_cents = way["fee_cents"] if way["id"] == "delivery" else 0
    now = dbm.utcnow()
    with g.db:
        order_id = _new_order_id(g.db)
        token = secrets.token_urlsafe(16)
        g.db.execute(
            "INSERT INTO orders(id, token, status, payment_status, payment_method, customer_name, email, phone, "
            "fulfilment, address, customer_note, subtotal_cents, delivery_cents, delivery_quote, total_cents, "
            "created_at, updated_at) VALUES (?, ?, 'new', 'unpaid', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, token, services().payments.name, name, email or None, phone, way["id"],
             address if way["id"] == "delivery" else None, note or None, quote["subtotal_cents"], delivery_cents,
             1 if (way["id"] == "delivery" and way["quote"]) else 0, quote["subtotal_cents"] + delivery_cents, now, now))
        for line in quote["lines"]:
            g.db.execute(
                "INSERT INTO order_items(order_id, product_id, product_name, sku, image_file, options_json, "
                "unit_price_cents, qty, line_total_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, line["product_id"], line["name"], line["sku"], line["image_file"],
                 json.dumps(line["options"], ensure_ascii=False), line["unit_cents"], line["qty"], line["line_cents"]))
    order = g.db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    order_url = url_for("store.order_page", order_id=order_id, t=token, new=1)
    print(f">> new order {order_id}: {quote['count']} item(s), R{order['total_cents'] / 100:.2f}")
    return jsonify({"order_id": order_id, "redirect": services().payments.start(order, order_url)})


def _new_order_id(conn):
    while True:
        order_id = "RN-" + "".join(secrets.choice(ORDER_ALPHABET) for _ in range(5))
        if conn.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone() is None:
            return order_id


# ── search engines + health ─────────────────────────────────────────────────
@bp.get("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin\nDisallow: /order/\nDisallow: /checkout\n" \
           f"Sitemap: {public_base()}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    base = public_base()
    urls = [f"{base}/", f"{base}/shop"]
    urls += [f"{base}/shop/{c['slug']}" for c in cat.list_categories(g.db, public=True)]
    urls += [f"{base}/product/{p['slug']}" for p in cat.list_products(g.db, public=True)]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    body += "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    return Response(body, mimetype="application/xml")


@bp.get("/healthz")
def healthz():
    g.db.execute("SELECT 1").fetchone()
    return {"ok": True}
