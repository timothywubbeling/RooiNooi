"""The shop customers see: home, categories, product pages, bag, checkout and order page."""
import json
import re
import secrets

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

from . import catalogue as cat
from . import db as dbm
from . import images
from .helpers import parse_money, public_base, rands, services
from .orders import PaymentError, get_order, mark_paid, refresh_payment, start_payment
from .payment import PAID, verify_webhook_signature
from .pudo import PudoError

bp = Blueprint("store", __name__)

ORDER_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


PUDO_FINDER = "https://www.pudo.co.za"
SHIPPING = (("pudo", "PUDO locker", "locker"), ("delivery", "Delivery", "address"))


def fulfilment_options(settings, online=False):
    """The ways to get an order, as switched on in Settings.

    With card payments the full amount is charged up front, so a way whose cost is
    "to be confirmed" (no fixed fee) is not offered until a fee is set.
    """
    out = []
    if settings.get("collect_enabled") == "1":
        out.append({"id": "collect", "label": "Collect", "note": settings.get("collect_note") or "",
                    "fee_cents": 0, "quote": False, "needs": None})
    for key, label, needs in SHIPPING:
        if settings.get(f"{key}_enabled") != "1":
            continue
        fee_text = (settings.get(f"{key}_fee") or "").strip()
        fee = parse_money(fee_text) if fee_text else None
        if fee is None and online:
            continue
        out.append({"id": key, "label": label, "note": settings.get(f"{key}_note") or "",
                    "fee_cents": fee or 0, "quote": fee is None, "needs": needs})
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
    svc = services()
    return {"S": settings, "menu": menu, "store_open": settings.get("store_open") == "1",
            "pay_online": svc.payments.online, "pudo_ready": svc.pudo is not None}


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
    online = services().payments.online
    return render_template("store/checkout.html", ways=fulfilment_options(settings, online), online=online,
                           pudo_finder=PUDO_FINDER)


def _order_for(order_id, token):
    order = get_order(g.db, order_id)
    if order is None or not token or not secrets.compare_digest(str(token), order["token"]):
        abort(404)
    return order


@bp.get("/order/<order_id>")
def order_page(order_id):
    order = _order_for(order_id, request.args.get("t", ""))
    came_back = request.args.get("paid") == "1"
    if order["payment_status"] != "paid" and (order["status"] == "pending" or came_back):
        order = refresh_payment(g.db, order)
    items = g.db.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order["id"],)).fetchall()
    options = {i["id"]: cat.order_options(i) for i in items}
    return render_template(
        "store/order.html", order=order, items=items, options=options,
        just_placed=request.args.get("new") == "1" or came_back,
        came_back_unpaid=came_back and order["payment_status"] != "paid",
        cancelled=request.args.get("cancelled") == "1", failed=request.args.get("failed") == "1",
        pay_error=request.args.get("payerr") == "1", online=services().payments.online)


@bp.post("/order/<order_id>/pay")
def order_pay(order_id):
    """Send the customer (back) to Yoco for an unpaid card order."""
    order = _order_for(order_id, request.args.get("t", ""))
    if order["payment_status"] == "paid" or order["status"] == "cancelled" or not services().payments.online:
        return redirect(url_for("store.order_page", order_id=order["id"], t=order["token"]))
    try:
        return redirect(start_payment(g.db, order))
    except PaymentError:
        return redirect(url_for("store.order_page", order_id=order["id"], t=order["token"], payerr=1))


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

    ways = {w["id"]: w for w in fulfilment_options(settings, services().payments.online)}
    way = ways.get(str(data.get("fulfilment") or ""))
    if way is None:
        errors["fulfilment"] = "Please choose collection or delivery."
    address = str(customer.get("address") or "").strip()[:400]
    locker = _clean(customer.get("locker"), 200)
    locker_code = _clean(customer.get("locker_code"), 20).upper()
    terminal = None
    if way and way["needs"] == "address" and len(address) < 8:
        errors["address"] = "Please enter the delivery address."
    if way and way["needs"] == "locker":
        pudo = services().pudo
        if pudo is not None and locker_code:
            try:
                found = pudo.locker(locker_code)
            except PudoError:
                found = None
            if found:
                terminal = found["code"]
                locker = f"{found['name']} ({found['code']}), {found['address']}"[:200]
        if len(locker) < 3:
            errors["locker"] = "Please choose the PUDO locker to send it to."
    if errors:
        return jsonify({"error": next(iter(errors.values())), "fields": errors}), 400

    if not services().order_limiter.allow(_client()):
        return jsonify({"error": "Too many orders from this device. Please contact us on WhatsApp."}), 429

    payments = services().payments
    delivery_cents = way["fee_cents"]
    where = address if way["needs"] == "address" else (locker if way["needs"] == "locker" else None)
    # Card orders wait as "pending" until Yoco confirms payment; then they become New.
    status = "pending" if payments.online else "new"
    now = dbm.utcnow()
    with g.db:
        order_id = _new_order_id(g.db)
        token = secrets.token_urlsafe(16)
        g.db.execute(
            "INSERT INTO orders(id, token, status, payment_status, payment_method, customer_name, email, phone, "
            "fulfilment, address, pudo_terminal, customer_note, subtotal_cents, delivery_cents, delivery_quote, "
            "total_cents, created_at, updated_at) VALUES (?, ?, ?, 'unpaid', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, token, status, payments.name, name, email or None, phone, way["id"], where, terminal,
             note or None, quote["subtotal_cents"], delivery_cents, 1 if way["quote"] else 0,
             quote["subtotal_cents"] + delivery_cents, now, now))
        for line in quote["lines"]:
            g.db.execute(
                "INSERT INTO order_items(order_id, product_id, product_name, sku, image_file, options_json, "
                "unit_price_cents, qty, line_total_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, line["product_id"], line["name"], line["sku"], line["image_file"],
                 json.dumps(line["options"], ensure_ascii=False), line["unit_cents"], line["qty"], line["line_cents"]))
    order = get_order(g.db, order_id)
    print(f">> new order {order_id}: {quote['count']} item(s), R{order['total_cents'] / 100:.2f} ({status})")
    if not payments.online:
        return jsonify({"order_id": order_id, "redirect": url_for("store.order_page", order_id=order_id, t=token, new=1)})
    try:
        target = start_payment(g.db, order)
    except PaymentError:
        target = url_for("store.order_page", order_id=order_id, t=token, payerr=1)
    return jsonify({"order_id": order_id, "redirect": target})


@bp.get("/api/pudo/lockers")
def pudo_lockers():
    """PUDO lockers for the checkout picker: [code, name, address, town]."""
    pudo = services().pudo
    if pudo is None:
        return jsonify({"lockers": []})
    try:
        lockers = pudo.lockers()
    except PudoError:
        return jsonify({"lockers": [], "error": "The PUDO locker list is not available right now."}), 503
    resp = jsonify({"lockers": [[l["code"], l["name"], l["address"], l["town"]] for l in lockers]})
    resp.headers["Cache-Control"] = "public, max-age=21600"
    return resp


# ── Yoco ────────────────────────────────────────────────────────────────────
@bp.post("/webhook/yoco")
def yoco_webhook():
    """Yoco tells us a payment went through. Signature-checked once the signing secret is saved."""
    body = request.get_data(cache=False)
    secret = dbm.get_setting(g.db, "yoco_webhook_secret") or services().cfg.get("yoco_webhook_secret") or ""
    signed = bool(secret) and verify_webhook_signature(secret, request.headers, body)
    if secret and not signed:
        print("!! Yoco webhook with a bad signature ignored")
        return jsonify({"error": "bad signature"}), 401
    try:
        event = json.loads(body or b"{}")
    except ValueError:
        return jsonify({"error": "bad json"}), 400
    if event.get("type") != "payment.succeeded":
        return jsonify({"ok": True})
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    order = get_order(g.db, meta.get("orderId") or payload.get("externalId"))
    if order is None and meta.get("checkoutId"):
        order = g.db.execute("SELECT * FROM orders WHERE checkout_id = ?", (meta["checkoutId"],)).fetchone()
    if order is None:
        print(f"!! Yoco payment for an unknown order: {str(meta)[:200]}")
        return jsonify({"ok": True})
    amount = payload.get("amount")
    if amount is not None and int(amount) != int(order["total_cents"]):
        print(f"!! Yoco payment for {order['id']} was {amount}, expected {order['total_cents']}; not marked paid")
        return jsonify({"ok": True})
    if not signed:
        # No signing secret saved yet: only believe Yoco's own answer about the checkout.
        if services().payments.checkout_status(meta.get("checkoutId") or order["checkout_id"]) != PAID:
            print(f"!! unsigned Yoco webhook for {order['id']} could not be confirmed")
            return jsonify({"ok": True})
    mark_paid(g.db, order["id"])
    return jsonify({"ok": True})


@bp.route("/dev/fake-pay/<checkout_id>", methods=["GET", "POST"])
def fake_pay(checkout_id):
    """Pretend Yoco page, only when SHOP_FAKE_PAYMENTS=1 (local previews and tests)."""
    payments = services().payments
    if not payments.is_fake:
        abort(404)
    entry = payments.checkouts.get(checkout_id)
    if entry is None:
        abort(404)
    if request.method == "POST":
        result = request.form.get("result")
        payments.set_status(checkout_id, PAID if result == "pay" else "failed")
        return redirect(entry["success_url"] if result == "pay" else entry["cancel_url"])
    return (f"<!doctype html><title>Pretend Yoco</title><body style='font-family:sans-serif;padding:40px'>"
            f"<h1>Pretend card payment</h1><p>Order {entry['order_id']}: R{entry['amount'] / 100:.2f}</p>"
            f"<form method=post><button name=result value=pay>Pay</button> "
            f"<button name=result value=cancel>Cancel</button></form>")


def _new_order_id(conn):
    while True:
        order_id = "RN-" + "".join(secrets.choice(ORDER_ALPHABET) for _ in range(5))
        if conn.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone() is None:
            return order_id


# ── search engines + health ─────────────────────────────────────────────────
@bp.get("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin\nDisallow: /order/\nDisallow: /checkout\nDisallow: /webhook/\n" \
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
    return {"ok": True, "commit": services().commit}   # which update the server runs
