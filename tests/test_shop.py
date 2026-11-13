"""Checks run by GitHub before every deploy (and locally with `pytest`).

They use a throwaway SQLite database, so they never touch the live shop.
"""
import io
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JPEG = (ROOT / "seed" / "images" / "napkin-the-woodland.jpg").read_bytes()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from shop import create_app
    return create_app()


@pytest.fixture()
def client(app):
    return app.test_client()


def db(app):
    return app.extensions["shop"].db.connect()


def csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def cracker_options(conn, filling="Dog toy", name="Ouma"):
    pid = conn.execute("SELECT id FROM products WHERE slug = 'customised-christmas-cracker'").fetchone()["id"]
    opts = {}
    for g in conn.execute("SELECT id, name, kind FROM option_groups WHERE product_id = ?", (pid,)).fetchall():
        if g["kind"] == "text":
            opts[str(g["id"])] = name
        elif g["name"] == "Filling":
            opts[str(g["id"])] = conn.execute("SELECT id FROM option_values WHERE group_id = ? AND label = ?",
                                              (g["id"], filling)).fetchone()["id"]
        else:
            opts[str(g["id"])] = conn.execute("SELECT id FROM option_values WHERE group_id = ? ORDER BY sort_order",
                                              (g["id"],)).fetchone()["id"]
    return pid, opts


def login(client, app):
    client.post("/admin/login", data={"password": "1234"})
    token = csrf(client.get("/admin/password").get_data(as_text=True))
    client.post("/admin/password", data={"csrf_token": token, "current": "1234", "new": "test-pass-99",
                                         "confirm": "test-pass-99"})
    return csrf(client.get("/admin/settings").get_data(as_text=True))


@pytest.mark.parametrize("path", ["/", "/shop", "/shop/premade-boxes", "/shop/napkins", "/checkout",
                                  "/product/customised-christmas-cracker", "/product/the-full-house",
                                  "/product/the-noel-napkins", "/robots.txt", "/sitemap.xml", "/healthz"])
def test_pages_load(client, path):
    assert client.get(path).status_code == 200


def test_missing_page(client):
    assert client.get("/nope").status_code == 404


def test_photos_are_served_from_the_database(client):
    html = client.get("/").get_data(as_text=True)
    src = re.search(r'src="(/media/[0-9a-f]{20}\.jpg)"', html).group(1)
    r = client.get(src)
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:3] == b"\xff\xd8\xff"
    assert "immutable" in r.headers["Cache-Control"]


def test_cracker_price_and_minimum(app, client):
    pid, opts = cracker_options(db(app))
    q = client.post("/api/quote", json={"lines": [{"product_id": pid, "qty": 2, "options": opts}]}).get_json()
    assert q["lines"][0]["unit_cents"] == 790 + 2000 + 100      # cracker + dog toy + name
    assert not q["ok"] and "minimum of 4" in q["problems"][0]["message"]
    q = client.post("/api/quote", json={"lines": [{"product_id": pid, "qty": 4, "options": opts}]}).get_json()
    assert q["ok"] and q["subtotal_cents"] == 4 * 2890


def test_missing_choice_is_refused(app, client):
    pid, _ = cracker_options(db(app))
    q = client.post("/api/quote", json={"lines": [{"product_id": pid, "qty": 4, "options": {}}]}).get_json()
    assert q["lines"][0]["error"] and not q["ok"]


def test_place_order_and_manage_it(app, client):
    conn = db(app)
    pid, opts = cracker_options(conn)
    nap = conn.execute("SELECT id FROM products WHERE slug = 'the-woodland-napkins'").fetchone()["id"]
    r = client.post("/api/orders", json={
        "lines": [{"product_id": pid, "qty": 4, "options": opts}, {"product_id": nap, "qty": 1, "options": {}}],
        "fulfilment": "delivery",
        "customer": {"name": "Test Person", "phone": "082 000 0000", "address": "1 Test Street, Pretoria"}})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (data["order_id"],)).fetchone()
    assert order["total_cents"] == 4 * 2890 + 4000 and order["payment_status"] == "unpaid"
    assert client.get(data["redirect"]).status_code == 200
    assert client.get(f"/order/{data['order_id']}?t=wrong").status_code == 404

    token = login(client, app)
    assert data["order_id"].encode() in client.get("/admin/").data
    assert client.get(f"/admin/orders/{data['order_id']}").status_code == 200
    client.post(f"/admin/orders/{data['order_id']}/payment", data={"csrf_token": token, "paid": "1"})
    client.post(f"/admin/orders/{data['order_id']}/edit", data={"csrf_token": token, "admin_note": "x",
                                                                "delivery_fee": "95"})
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (data["order_id"],)).fetchone()
    assert order["payment_status"] == "paid" and order["total_cents"] == 4 * 2890 + 4000 + 9500


def test_order_needs_phone(app, client):
    conn = db(app)
    nap = conn.execute("SELECT id FROM products WHERE slug = 'the-woodland-napkins'").fetchone()["id"]
    r = client.post("/api/orders", json={"lines": [{"product_id": nap, "qty": 1, "options": {}}],
                                         "fulfilment": "collect", "customer": {"name": "A B", "phone": "12"}})
    assert r.status_code == 400 and "phone" in r.get_json()["fields"]


def test_admin_needs_login_and_new_password(client):
    assert client.get("/admin/").headers["Location"].startswith("/admin/login")
    client.post("/admin/login", data={"password": "1234"})
    assert client.get("/admin/").headers["Location"] == "/admin/password"


def test_admin_pages_load(app, client):
    login(client, app)
    for path in ["/admin/", "/admin/products", "/admin/products/new", "/admin/products/1",
                 "/admin/categories", "/admin/orders", "/admin/orders?show=all", "/admin/settings"]:
        assert client.get(path).status_code == 200, path


def test_edit_product_options_and_photos(app, client):
    token = login(client, app)
    html = client.get("/admin/products/2").get_data(as_text=True)
    data = json.loads(re.search(r'id="form-data">(.*?)</script>', html, re.S).group(1))
    opts = data["options"]
    opts[0]["values"][1]["active"] = False
    opts.append({"id": None, "name": "Gift note", "kind": "text", "required": False, "help": "", "price": "5",
                 "max_length": 50, "values": []})
    form = {"csrf_token": token, "name": "The Full House", "price": "185.50", "category_id": "2", "min_qty": "1",
            "status": "live", "image_order": ",".join(i["ref"] for i in data["images"]) + ",new:0",
            "options_json": json.dumps(opts), "photos": (io.BytesIO(JPEG + b"x"), "x.jpg")}
    r = client.post("/admin/products/2", data=form, content_type="multipart/form-data")
    assert r.status_code == 302
    html = client.get("/admin/products/2").get_data(as_text=True)
    after = json.loads(re.search(r'id="form-data">(.*?)</script>', html, re.S).group(1))
    assert len(after["images"]) == len(data["images"]) + 1
    assert [v["id"] for v in after["options"][0]["values"]] == [v["id"] for v in data["options"][0]["values"]]
    page = client.get("/product/the-full-house").data
    assert b"R185.50" in page and b"Gift note" in page


def test_csrf_is_required(app, client):
    login(client, app)
    assert client.post("/admin/api/store-open", json={"open": False}).status_code == 400
