"""Admin pages: built for a phone first, work fine on a laptop."""
import json
from datetime import timedelta

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from . import auth
from . import catalogue as cat
from . import db as dbm
from .helpers import local_now, local_to_utc_str, parse_money, public_base, services
from .images import ImageError, is_stored, store_upload
from .orders import mark_paid, refresh_payment, refresh_recent_pending
from .payment import PaymentError
from .pudo import SIZES as PUDO_SIZES, PudoError

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAGE_SIZE = 30
ORDER_STATUSES = {"new": "New", "making": "Making", "ready": "Ready", "done": "Done", "cancelled": "Cancelled"}
OPEN_STATUSES = ("new", "making", "ready")


@bp.context_processor
def admin_context():
    new_orders = 0
    if getattr(g, "db", None) is not None and session.get("admin"):
        new_orders = g.db.execute("SELECT COUNT(*) AS n FROM orders WHERE status = 'new'").fetchone()["n"]
    return {"csrf_token": auth.csrf_token, "nav": request.endpoint, "ORDER_STATUSES": ORDER_STATUSES,
            "PRODUCT_STATUSES": cat.STATUSES, "new_orders": new_orders}


def _json_error(message, code=400):
    return jsonify({"error": message}), code


def _int(value, lo, hi, default=None):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if lo <= number <= hi else default


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _text(value, limit):
    """Multi-line text: keep line breaks, trim each line."""
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()[:limit]


def _safe_next(target):
    if target and target.startswith("/admin") and not target.startswith("//"):
        return target
    return url_for("admin.dashboard")


# ── login ──────────────────────────────────────────────────────────────────
@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.is_logged_in():
        return redirect(url_for("admin.dashboard"))
    error = None
    if request.method == "POST":
        if auth.locked_out():
            error = "Too many wrong passwords. Wait 10 minutes and try again."
        elif auth.check_password(g.db, request.form.get("password")):
            auth.clear_failures()
            auth.log_in(g.db)
            return redirect(_safe_next(request.args.get("next")))
        else:
            auth.record_failure()
            error = "That password is not right."
    return render_template("admin/login.html", error=error)


@bp.post("/logout")
@auth.login_required
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@bp.route("/password", methods=["GET", "POST"])
@auth.login_required
def change_password():
    forced = dbm.get_setting(g.db, "admin_password_must_change") == "1"
    error = None
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not auth.check_password(g.db, current):
            error = "Your current password is not right."
        elif len(new) < 6:
            error = "Use at least 6 characters."
        elif new in ("1234", "123456", "password", "admin", "rooinooi") or auth.check_password(g.db, new):
            error = "Pick a password that is harder to guess."
        elif new != confirm:
            error = "The two new passwords do not match."
        else:
            with g.db:
                auth.set_password(g.db, new)
            auth.log_in(g.db)
            flash("Password changed. Other phones have been signed out.")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/password.html", forced=forced, error=error)


# ── dashboard ──────────────────────────────────────────────────────────────
ORDER_LIST_SQL = """
    SELECT o.*,
           (SELECT group_concat(oi.qty || '× ' || oi.product_name, ', ')
              FROM order_items oi WHERE oi.order_id = o.id) AS summary,
           (SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = o.id) AS units
      FROM orders o
"""


def _period(start_local, end_local):
    row = g.db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS total, "
        "COALESCE(SUM(CASE WHEN payment_status = 'paid' THEN total_cents ELSE 0 END), 0) AS paid "
        "FROM orders WHERE status NOT IN ('cancelled', 'pending') AND created_at >= ? AND created_at < ?",
        (local_to_utc_str(start_local), local_to_utc_str(end_local))).fetchone()
    return dict(row)


def to_make():
    """What is still to be made for open orders: products and the options picked."""
    rows = g.db.execute(
        "SELECT oi.product_name, oi.qty, oi.options_json FROM order_items oi "
        "JOIN orders o ON o.id = oi.order_id WHERE o.status IN ('new', 'making')").fetchall()
    products = {}
    for r in rows:
        entry = products.setdefault(r["product_name"], {"name": r["product_name"], "qty": 0, "groups": {}})
        entry["qty"] += r["qty"]
        for opt in cat.order_options(r):
            group = entry["groups"].setdefault(opt.get("group", ""), {})
            group[opt.get("value", "")] = group.get(opt.get("value", ""), 0) + r["qty"]
    out = []
    for entry in sorted(products.values(), key=lambda e: -e["qty"]):
        entry["groups"] = [(name, sorted(values.items(), key=lambda kv: -kv[1]))
                           for name, values in entry["groups"].items()]
        out.append(entry)
    return out


@bp.get("/")
@auth.login_required
def dashboard():
    if refresh_recent_pending(g.db):
        flash("Yoco confirmed a card payment that hadn't come through yet. That order is now New.")
    now = local_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    today = _period(day_start, day_start + timedelta(days=1))
    month = _period(month_start, next_month)
    counts = {r["status"]: r["n"] for r in g.db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")}
    unpaid = g.db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS total FROM orders "
        "WHERE payment_status = 'unpaid' AND status NOT IN ('cancelled', 'pending')").fetchone()
    open_orders = g.db.execute(
        ORDER_LIST_SQL + " WHERE o.status IN ('new', 'making', 'ready') "
        "ORDER BY CASE o.status WHEN 'new' THEN 0 WHEN 'making' THEN 1 ELSE 2 END, o.created_at LIMIT 12").fetchall()
    products = cat.list_products(g.db)
    return render_template(
        "admin/dashboard.html", today=today, month=month, counts=counts, unpaid=unpaid, now_local=now,
        open_orders=open_orders, make=to_make(), store_open=dbm.get_setting(g.db, "store_open") == "1",
        live=sum(1 for p in products if p["status"] == "live"),
        soldout=sum(1 for p in products if p["status"] == "soldout"),
        no_photo=[p for p in products if not p["image"] and p["status"] != "hidden"],
    )


@bp.post("/api/store-open")
@auth.login_required
def api_store_open():
    data = request.get_json(silent=True) or {}
    with g.db:
        dbm.set_setting(g.db, "store_open", "1" if data.get("open") else "0")
    return jsonify({"open": dbm.get_setting(g.db, "store_open") == "1"})


# ── products ───────────────────────────────────────────────────────────────
@bp.get("/products")
@auth.login_required
def products():
    rows = cat.list_products(g.db)
    return render_template("admin/products.html", products=rows, labels=cat.price_labels(g.db, rows),
                           categories=cat.list_categories(g.db))


@bp.post("/api/products/<int:product_id>/status")
@auth.login_required
def api_product_status(product_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in cat.STATUSES:
        return _json_error("Unknown status.")
    with g.db:
        changed = g.db.execute("UPDATE products SET status = ?, updated_at = ? WHERE id = ?",
                               (status, dbm.utcnow(), product_id)).rowcount
    if not changed:
        return _json_error("That product does not exist.", 404)
    return jsonify({"status": status, "label": cat.STATUSES[status]})


@bp.get("/api/products/<int:product_id>/options")
@auth.login_required
def api_product_options(product_id):
    """For "Copy options from another product" in the product editor."""
    if cat.get_product(g.db, product_id) is None:
        return _json_error("That product does not exist.", 404)
    options = cat.options_for_editor(g.db, product_id)
    for group in options:
        group["id"] = None
        for value in group["values"]:
            value["id"] = None
            value["image"] = f"file:{value['image_file']}" if value["image_file"] else ""
    return jsonify({"options": options})


def _blank_product():
    return {"name": "", "category_id": None, "price": "", "summary": "", "description": "", "includes": "",
            "min_qty": 1, "status": "live", "featured": False, "sku": "", "slug": "", "meta_title": "",
            "meta_description": "", "tags": ""}


def _product_values(p):
    return {"name": p["name"], "category_id": p["category_id"], "price": f"{p['price_cents'] / 100:.2f}",
            "summary": p["summary"] or "", "description": p["description"] or "", "includes": p["includes"] or "",
            "min_qty": p["min_qty"], "status": p["status"], "featured": bool(p["featured"]), "sku": p["sku"] or "",
            "slug": p["slug"], "meta_title": p["meta_title"] or "", "meta_description": p["meta_description"] or "",
            "tags": p["tags"] or ""}


def _product_form(product):
    """Shared by new + edit. Returns a response."""
    values = _product_values(product) if product else _blank_product()
    images = [{"ref": f"img:{i['id']}", "file": i["file"]} for i in cat.product_images(g.db, product["id"])] \
        if product else []
    options = cat.options_for_editor(g.db, product["id"]) if product else []
    errors = []

    if request.method == "POST":
        f = request.form
        values = {
            "name": _clean(f.get("name"), 100),
            "category_id": _int(f.get("category_id"), 1, 10**9),
            "price": f.get("price", "").strip(),
            "summary": _clean(f.get("summary"), 120),
            "description": _text(f.get("description"), 5000),
            "includes": _text(f.get("includes"), 600),
            "min_qty": _int(f.get("min_qty"), 1, 500),
            "status": f.get("status") if f.get("status") in cat.STATUSES else "live",
            "featured": f.get("featured") == "1",
            "sku": _clean(f.get("sku"), 40),
            "slug": cat.slugify(f.get("slug")) if f.get("slug", "").strip() else "",
            "meta_title": _clean(f.get("meta_title"), 120),
            "meta_description": _clean(f.get("meta_description"), 320),
            "tags": _clean(f.get("tags"), 300),
        }
        price_cents = parse_money(values["price"], allow_zero=True) if values["price"] else None
        if not values["name"]:
            errors.append("Give the product a name.")
        if price_cents is None:
            errors.append("Enter a price like 145.00 (or 0 if the options carry the price).")
        if values["min_qty"] is None:
            errors.append("Minimum per order must be a number from 1 to 500.")
            values["min_qty"] = 1
        if values["category_id"] and cat.get_category(g.db, values["category_id"]) is None:
            values["category_id"] = None
        raw_options = f.get("options_json", "[]")
        try:
            groups = cat.parse_options(raw_options)
        except cat.OptionError as exc:
            errors.append(str(exc))
            groups = None
        try:
            options = json.loads(raw_options)
        except ValueError:
            pass

        # Photo list as arranged on the page: img:<id> (already saved), new:<n> (uploaded now),
        # file:<name> (a photo copied over with another product's options).
        order_refs = [r for r in f.get("image_order", "").split(",") if r]
        uploaded = {}
        if not errors:
            for n, upload in enumerate(request.files.getlist("photos")):
                if upload and upload.filename:
                    try:
                        uploaded[f"new:{n}"] = store_upload(g.db, upload)
                    except ImageError as exc:
                        errors.append(f"Photo {n + 1}: {exc}")

        if errors:
            if any(r.startswith("new:") for r in order_refs):
                errors.append("Photos you just added were not saved. Please add them again.")
            keep = {i["ref"]: i for i in images}
            images = [keep[r] for r in order_refs if r in keep]
            return render_template("admin/product_form.html", product=product, values=values, images=images,
                                   options=options, errors=errors, categories=cat.list_categories(g.db),
                                   others=_other_products(product))

        now = dbm.utcnow()
        with g.db:
            slug = cat.unique_slug(g.db, "products", values["slug"] or values["name"],
                                   exclude_id=product["id"] if product else None)
            fields = (values["category_id"], values["name"], slug, values["sku"] or None, values["summary"] or None,
                      values["description"] or None, values["includes"] or None, price_cents, values["min_qty"],
                      values["status"], 1 if values["featured"] else 0, values["meta_title"] or None,
                      values["meta_description"] or None, values["tags"] or None)
            if product:
                product_id = product["id"]
                g.db.execute(
                    "UPDATE products SET category_id=?, name=?, slug=?, sku=?, summary=?, description=?, includes=?, "
                    "price_cents=?, min_qty=?, status=?, featured=?, meta_title=?, meta_description=?, tags=?, "
                    "updated_at=? WHERE id=?", fields + (now, product_id))
            else:
                last = g.db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM products WHERE category_id IS NOT DISTINCT FROM ?",
                                    (values["category_id"],)).fetchone()["n"]
                product_id = g.db.execute(
                    "INSERT INTO products(category_id, name, slug, sku, summary, description, includes, price_cents, "
                    "min_qty, status, featured, meta_title, meta_description, tags, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", fields + (last, now, now)).lastrowid

            existing = {f"img:{r['id']}": r["id"] for r in
                        g.db.execute("SELECT id FROM product_images WHERE product_id = ?", (product_id,))}
            ref_to_id = {}
            for ref in order_refs:
                if ref in ref_to_id:
                    continue
                if ref in existing:
                    ref_to_id[ref] = existing[ref]
                elif ref in uploaded:
                    ref_to_id[ref] = g.db.execute(
                        "INSERT INTO product_images(product_id, file) VALUES (?, ?)",
                        (product_id, uploaded[ref])).lastrowid
                elif ref.startswith("file:") and is_stored(g.db, ref[5:]):
                    ref_to_id[ref] = g.db.execute(
                        "INSERT INTO product_images(product_id, file) VALUES (?, ?)",
                        (product_id, ref[5:])).lastrowid
            for ref, image_id in existing.items():
                if image_id not in ref_to_id.values():
                    g.db.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
            for position, image_id in enumerate(dict.fromkeys(ref_to_id.values())):
                g.db.execute("UPDATE product_images SET sort_order = ? WHERE id = ?", (position, image_id))

            cat.save_options(g.db, product_id, groups, lambda ref: ref_to_id.get(ref))

        flash("Product saved." if product else "Product added.")
        if request.form.get("after") == "view":
            return redirect(url_for("store.product", slug=slug))
        return redirect(url_for("admin.product_edit", product_id=product_id))

    if not product and request.args.get("category"):
        values["category_id"] = _int(request.args.get("category"), 1, 10**9)
    return render_template("admin/product_form.html", product=product, values=values, images=images,
                           options=options, errors=errors, categories=cat.list_categories(g.db),
                           others=_other_products(product))


def _other_products(product):
    return [{"id": p["id"], "name": p["name"]} for p in cat.list_products(g.db)
            if p["option_count"] and (product is None or p["id"] != product["id"])]


@bp.route("/products/new", methods=["GET", "POST"])
@auth.login_required
def product_new():
    return _product_form(None)


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
@auth.login_required
def product_edit(product_id):
    product = cat.get_product(g.db, product_id)
    if product is None:
        abort(404)
    return _product_form(product)


@bp.post("/products/<int:product_id>/duplicate")
@auth.login_required
def product_duplicate(product_id):
    with g.db:
        new_id = cat.duplicate_product(g.db, product_id)
    if new_id is None:
        abort(404)
    flash("Copy made. It is hidden from the shop until you switch it on.")
    return redirect(url_for("admin.product_edit", product_id=new_id))


@bp.post("/products/<int:product_id>/delete")
@auth.login_required
def product_delete(product_id):
    product = cat.get_product(g.db, product_id)
    if product is None:
        abort(404)
    with g.db:
        g.db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    flash(f"{product['name']} deleted. Past orders still show what was bought.")
    return redirect(url_for("admin.products"))


@bp.post("/products/<int:product_id>/move")
@auth.login_required
def product_move(product_id):
    product = cat.get_product(g.db, product_id)
    if product is None:
        abort(404)
    _move(g.db.execute("SELECT id FROM products WHERE category_id IS NOT DISTINCT FROM ? ORDER BY sort_order, lower(name)",
                       (product["category_id"],)).fetchall(), product_id, request.form.get("dir"), "products")
    return redirect(url_for("admin.products") + f"#p{product_id}")


def _move(rows, row_id, direction, table):
    ids = [r["id"] for r in rows]
    if row_id not in ids:
        return
    i = ids.index(row_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    with g.db:
        for position, rid in enumerate(ids):
            g.db.execute(f"UPDATE {table} SET sort_order = ? WHERE id = ?", (position, rid))


# ── categories ─────────────────────────────────────────────────────────────
@bp.get("/categories")
@auth.login_required
def categories():
    return render_template("admin/categories.html", categories=cat.list_categories(g.db))


@bp.post("/categories")
@auth.login_required
def category_add():
    name = _clean(request.form.get("name"), 60)
    if not name:
        flash("Give the category a name.")
        return redirect(url_for("admin.categories"))
    now = dbm.utcnow()
    with g.db:
        last = g.db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM categories").fetchone()["n"]
        g.db.execute(
            "INSERT INTO categories(name, slug, blurb, sort_order, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (name, cat.unique_slug(g.db, "categories", name), _text(request.form.get("blurb"), 600) or None,
             last, now, now))
    flash(f"{name} added. Now add products to it.")
    return redirect(url_for("admin.categories"))


@bp.post("/categories/<int:category_id>")
@auth.login_required
def category_update(category_id):
    if cat.get_category(g.db, category_id) is None:
        abort(404)
    f = request.form
    name = _clean(f.get("name"), 60)
    if not name:
        flash("A category needs a name.")
        return redirect(url_for("admin.categories"))
    with g.db:
        g.db.execute(
            "UPDATE categories SET name=?, slug=?, blurb=?, meta_title=?, meta_description=?, active=?, updated_at=? "
            "WHERE id=?",
            (name, cat.unique_slug(g.db, "categories", f.get("slug") or name, exclude_id=category_id),
             _text(f.get("blurb"), 600) or None, _clean(f.get("meta_title"), 120) or None,
             _clean(f.get("meta_description"), 320) or None, 1 if f.get("active") == "1" else 0,
             dbm.utcnow(), category_id))
    flash(f"{name} saved.")
    return redirect(url_for("admin.categories") + f"#c{category_id}")


@bp.post("/categories/<int:category_id>/move")
@auth.login_required
def category_move(category_id):
    _move(g.db.execute("SELECT id FROM categories ORDER BY sort_order, id").fetchall(), category_id,
          request.form.get("dir"), "categories")
    return redirect(url_for("admin.categories") + f"#c{category_id}")


@bp.post("/categories/<int:category_id>/delete")
@auth.login_required
def category_delete(category_id):
    c = cat.get_category(g.db, category_id)
    if c is None:
        abort(404)
    with g.db:
        g.db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    flash(f"{c['name']} deleted. Its products are kept, without a category.")
    return redirect(url_for("admin.categories"))


# ── orders ─────────────────────────────────────────────────────────────────
ORDER_FILTERS = {
    "open": ("Open", "o.status IN ('new', 'making', 'ready')"),
    "new": ("New", "o.status = 'new'"),
    "making": ("Making", "o.status = 'making'"),
    "ready": ("Ready", "o.status = 'ready'"),
    "unpaid": ("Unpaid", "o.payment_status = 'unpaid' AND o.status NOT IN ('cancelled', 'pending')"),
    "pending": ("Awaiting card payment", "o.status = 'pending'"),
    "done": ("Done", "o.status = 'done'"),
    "cancelled": ("Cancelled", "o.status = 'cancelled'"),
    "all": ("All", "1 = 1"),
}


@bp.get("/orders")
@auth.login_required
def orders():
    refresh_recent_pending(g.db)
    show = request.args.get("show") if request.args.get("show") in ORDER_FILTERS else "open"
    q = _clean(request.args.get("q"), 60)
    page = _int(request.args.get("page"), 1, 10000, 1)
    where, args = [ORDER_FILTERS[show][1]], []
    if q:
        where.append("(lower(o.id) LIKE ? OR lower(o.customer_name) LIKE ? OR o.phone LIKE ? OR lower(o.email) LIKE ?)")
        args += [f"%{q.lower()}%"] * 4
    sql_where = " WHERE " + " AND ".join(where)
    total = g.db.execute("SELECT COUNT(*) AS n FROM orders o" + sql_where, args).fetchone()["n"]
    rows = g.db.execute(ORDER_LIST_SQL + sql_where + " ORDER BY o.created_at DESC LIMIT ? OFFSET ?",
                        args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    counts = {key: g.db.execute("SELECT COUNT(*) AS n FROM orders o WHERE " + cond).fetchone()["n"]
              for key, (_, cond) in ORDER_FILTERS.items()}
    return render_template("admin/orders.html", orders=rows, show=show, q=q, page=page, total=total,
                           pages=max(1, -(-total // PAGE_SIZE)), filters=ORDER_FILTERS, counts=counts)


def _get_order(order_id):
    order = g.db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        abort(404)
    return order


@bp.get("/orders/<order_id>")
@auth.login_required
def order_detail(order_id):
    order = _get_order(order_id)
    items = g.db.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()
    options = {i["id"]: cat.order_options(i) for i in items}
    link = url_for("store.order_page", order_id=order["id"], t=order["token"], _external=False)
    svc = services()
    return render_template("admin/order_detail.html", order=order, items=items, options=options,
                           customer_link=public_base() + link, pay_online=svc.payments.online,
                           pudo_ready=svc.pudo is not None, pudo_sizes=PUDO_SIZES,
                           pudo_size=dbm.get_setting(g.db, "pudo_size") or "S")


@bp.post("/orders/<order_id>/status")
@auth.login_required
def order_status(order_id):
    _get_order(order_id)
    status = request.form.get("status")
    if status not in ORDER_STATUSES:
        abort(400, "Unknown status.")
    with g.db:
        g.db.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, dbm.utcnow(), order_id))
    flash(f"Order {order_id} is now “{ORDER_STATUSES[status]}”.")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<order_id>/payment")
@auth.login_required
def order_payment(order_id):
    _get_order(order_id)
    if request.form.get("paid") == "1":
        mark_paid(g.db, order_id, how="manual")
    else:
        with g.db:
            g.db.execute("UPDATE orders SET payment_status = 'unpaid', paid_at = NULL, updated_at = ? WHERE id = ?",
                         (dbm.utcnow(), order_id))
    flash(f"Order {order_id} marked as {'paid' if request.form.get('paid') == '1' else 'not paid'}.")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<order_id>/check-payment")
@auth.login_required
def order_check_payment(order_id):
    """Ask Yoco directly whether a card order was paid."""
    order = refresh_payment(g.db, _get_order(order_id))
    flash("Yoco says it is paid." if order["payment_status"] == "paid" else
          "Yoco has no completed payment for this order yet.")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<order_id>/pudo")
@auth.login_required
def order_pudo_book(order_id):
    """Book the PUDO Locker-to-Locker shipment for this order."""
    order = _get_order(order_id)
    pudo = services().pudo
    back = redirect(url_for("admin.order_detail", order_id=order_id) + "#shipping")
    if pudo is None:
        flash("PUDO is not connected yet. Add PUDO_API_KEY in the Lambda settings.")
        return back
    if order["pudo_shipment_id"]:
        flash("This order already has a PUDO shipment.")
        return back
    terminal = _clean(request.form.get("terminal"), 20).upper()
    size = request.form.get("size") if request.form.get("size") in PUDO_SIZES else None
    if not terminal or not size:
        flash("Choose the parcel size and the customer's locker code (e.g. CG54).")
        return back
    s = dbm.all_settings(g.db)
    try:
        if not pudo.locker(terminal):
            flash(f"PUDO has no locker with the code {terminal}. Check the code.")
            return back
        shipment = pudo.create_shipment(order, terminal, size,
                                        {"name": s.get("store_name"), "email": s.get("email"), "phone": s.get("phone")})
    except PudoError as exc:
        flash(str(exc))
        return back
    with g.db:
        g.db.execute(
            "UPDATE orders SET pudo_terminal = ?, pudo_shipment_id = ?, pudo_waybill = ?, pudo_pin = ?, "
            "pudo_status = ?, status = CASE WHEN status IN ('new', 'making') THEN 'ready' ELSE status END, "
            "updated_at = ? WHERE id = ?",
            (terminal, shipment["id"], shipment["waybill"], shipment["pin"], shipment["status"], dbm.utcnow(), order_id))
    flash(f"PUDO shipment booked: waybill {shipment['waybill']}. Drop it in any PUDO locker with PIN {shipment['pin']}.")
    return back


@bp.get("/orders/<order_id>/pudo/label")
@auth.login_required
def order_pudo_label(order_id):
    order = _get_order(order_id)
    pudo = services().pudo
    if pudo is None or not order["pudo_shipment_id"]:
        abort(404)
    try:
        return redirect(pudo.label_url(order["pudo_shipment_id"]))
    except PudoError as exc:
        flash(str(exc))
        return redirect(url_for("admin.order_detail", order_id=order_id) + "#shipping")


@bp.post("/orders/<order_id>/edit")
@auth.login_required
def order_edit(order_id):
    """Private note, and the delivery cost once it has been agreed."""
    order = _get_order(order_id)
    note = _text(request.form.get("admin_note"), 2000)
    delivery = order["delivery_cents"]
    quote = order["delivery_quote"]
    if order["fulfilment"] in ("delivery", "pudo") and "delivery_fee" in request.form:
        fee = parse_money(request.form.get("delivery_fee"))
        if fee is None:
            flash("Enter the delivery cost like 80.00")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        delivery, quote = fee, 0
    with g.db:
        g.db.execute(
            "UPDATE orders SET admin_note = ?, delivery_cents = ?, delivery_quote = ?, total_cents = subtotal_cents + ?, "
            "updated_at = ? WHERE id = ?", (note or None, delivery, quote, delivery, dbm.utcnow(), order_id))
    flash("Order updated.")
    return redirect(url_for("admin.order_detail", order_id=order_id))


# ── settings ───────────────────────────────────────────────────────────────
SETTING_FIELDS = {
    "shop": {"store_name": 40, "tagline": 80, "announcement": 140, "meta_description": 300},
    "contact": {"phone": 30, "whatsapp": 30, "email": 120, "address": 200, "instagram": 200, "facebook": 200},
    "home": {"hero_kicker": 40, "hero_title": 30, "hero_script": 30, "about": 1200},
    "orders": {"collect_note": 300, "pudo_note": 300, "delivery_note": 300, "order_message": 800},
}
SWITCHES = {"shop": ["store_open"], "orders": ["collect_enabled", "pudo_enabled", "delivery_enabled"]}
MULTILINE = {"about", "order_message", "address"}


@bp.route("/settings", methods=["GET", "POST"])
@auth.login_required
def settings():
    errors = []
    if request.method == "POST":
        section = request.form.get("section")
        if section not in SETTING_FIELDS:
            abort(400)
        updates = {}
        for key, limit in SETTING_FIELDS[section].items():
            raw = request.form.get(key, "")
            updates[key] = _text(raw, limit) if key in MULTILINE else _clean(raw, limit)
        for key in SWITCHES.get(section, []):
            updates[key] = "1" if request.form.get(key) == "1" else "0"
        if section == "shop" and not updates["store_name"]:
            errors.append("The shop needs a name.")
        if section == "orders":
            for key, label in (("pudo_fee", "PUDO fee"), ("delivery_fee", "delivery fee")):
                fee = request.form.get(key, "").strip()
                if fee and parse_money(fee) is None:
                    errors.append(f"Enter the {label} like 80.00, or leave it empty to quote each order.")
                updates[key] = f"{parse_money(fee) / 100:.2f}" if fee and parse_money(fee) is not None else ""
            updates["pudo_size"] = request.form.get("pudo_size") if request.form.get("pudo_size") in PUDO_SIZES else "S"
            if "1" not in (updates["collect_enabled"], updates["pudo_enabled"], updates["delivery_enabled"]):
                errors.append("Switch on at least one of collection, PUDO or delivery, otherwise nobody can check out.")
        if section == "home":
            for key in ("hero_left", "hero_right"):
                upload = request.files.get(key)
                if upload and upload.filename:
                    try:
                        updates[key] = store_upload(g.db, upload)
                    except ImageError as exc:
                        errors.append(str(exc))
        if not errors:
            with g.db:
                for key, value in updates.items():
                    dbm.set_setting(g.db, key, value)
            flash("Settings saved.")
            return redirect(url_for("admin.settings") + f"#{section}")
    svc = services()
    s = dbm.all_settings(g.db)
    hidden = []
    if svc.payments.online:
        for key, label in (("pudo", "PUDO"), ("delivery", "Delivery")):
            if s.get(f"{key}_enabled") == "1" and not (s.get(f"{key}_fee") or "").strip():
                hidden.append(label)
    return render_template("admin/settings.html", s=s, errors=errors, payments=svc.payments,
                           base_url=public_base(), pudo=svc.pudo, pudo_sizes=PUDO_SIZES, hidden_ways=hidden,
                           webhook_on=bool(s.get("yoco_webhook_secret") or svc.cfg.get("yoco_webhook_secret")))


@bp.post("/yoco/connect")
@auth.login_required
def yoco_connect():
    """Register this shop's webhook with Yoco and keep the signing secret it returns."""
    payments = services().payments
    if not payments.online:
        flash("Add YOCO_SECRET_KEY in the Lambda settings first.")
        return redirect(url_for("admin.settings") + "#payments")
    url = public_base() + url_for("store.yoco_webhook")
    try:
        hook_id, secret = payments.register_webhook(url)
    except PaymentError as exc:
        flash(f"Could not connect payment notifications: {exc}")
        return redirect(url_for("admin.settings") + "#payments")
    with g.db:
        dbm.set_setting(g.db, "yoco_webhook_secret", secret)
        dbm.set_setting(g.db, "yoco_webhook_id", hook_id)
    flash("Payment notifications connected. Yoco will now tell the shop the moment a payment goes through.")
    return redirect(url_for("admin.settings") + "#payments")


@bp.get("/api/pudo/rates")
@auth.login_required
def api_pudo_rates():
    """Current PUDO Locker-to-Locker prices, to help set the shipping fee."""
    pudo = services().pudo
    if pudo is None:
        return _json_error("PUDO is not connected yet.")
    try:
        lockers = pudo.lockers()
        if not lockers:
            return _json_error("PUDO returned no lockers.")
        target = next((l for l in lockers if "pretoria" in l["town"].lower()), lockers[0])
        return jsonify({"to": f"{target['name']} ({target['code']})", "rates": pudo.rates(target["code"])})
    except PudoError as exc:
        return _json_error(str(exc))
