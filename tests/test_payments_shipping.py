"""Card payments (pretend Yoco) and PUDO shipping (pretend PUDO). Nothing real is charged or booked."""
import json
import re
import time

import pytest

from shop import db as dbm
from shop.payment import sign_for_tests

SECRET = "whsec_" + "dGVzdC1zaWduaW5nLXNlY3JldC0xMjM0NTY3ODkw"   # base64 of a test value


def make_app(tmp_path, monkeypatch, pay=True, pudo=True):
    monkeypatch.setenv("SHOP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SHOP_FAKE_PAYMENTS", "1" if pay else "0")
    monkeypatch.setenv("SHOP_FAKE_PUDO", "1" if pudo else "0")
    from shop import create_app
    app = create_app()
    conn = app.extensions["shop"].db.connect()
    with conn:
        dbm.set_setting(conn, "pudo_fee", "70.00")
    return app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


def conn(app):
    return app.extensions["shop"].db.connect()


def napkins(c):
    return c.execute("SELECT id FROM products WHERE slug = 'the-woodland-napkins'").fetchone()["id"]


def place(app, client, fulfilment="collect", **customer):
    body = {"name": "Test Person", "phone": "082 000 0000"}
    body.update(customer)
    r = client.post("/api/orders", json={"lines": [{"product_id": napkins(conn(app)), "qty": 2, "options": {}}],
                                         "fulfilment": fulfilment, "customer": body})
    return r


def login(client):
    client.post("/admin/login", data={"password": "1234"})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/password").get_data(as_text=True)).group(1)
    client.post("/admin/password", data={"csrf_token": tok, "current": "1234", "new": "test-pass-99",
                                         "confirm": "test-pass-99"})
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/settings").get_data(as_text=True)).group(1)


def test_card_order_waits_for_payment_then_becomes_new(app):
    client = app.test_client()
    r = place(app, client)
    data = r.get_json()
    assert r.status_code == 200 and data["redirect"].startswith("/dev/fake-pay/")
    order = conn(app).execute("SELECT * FROM orders WHERE id = ?", (data["order_id"],)).fetchone()
    assert order["status"] == "pending" and order["payment_status"] == "unpaid" and order["checkout_id"]

    # Not counted as a New order until paid.
    admin = app.test_client()
    login(admin)
    assert b"Awaiting payment" in admin.get("/admin/orders?show=pending").data
    assert data["order_id"].encode() not in admin.get("/admin/orders?show=open").data

    # The customer pays on the (pretend) Yoco page and comes back.
    r = client.post(data["redirect"], data={"result": "pay"})
    assert r.status_code == 302 and "paid=1" in r.headers["Location"]
    back = r.headers["Location"].replace("http://localhost", "")
    page = client.get(back)
    assert page.status_code == 200 and b"<strong>Paid.</strong>" in page.data
    order = conn(app).execute("SELECT * FROM orders WHERE id = ?", (data["order_id"],)).fetchone()
    assert order["payment_status"] == "paid" and order["status"] == "new" and order["payment_method"] == "yoco"


def test_cancelled_payment_can_be_retried(app):
    client = app.test_client()
    data = place(app, client).get_json()
    r = client.post(data["redirect"], data={"result": "cancel"})
    back = r.headers["Location"].replace("http://localhost", "")
    page = client.get(back).get_data(as_text=True)
    assert "cancelled" in page and "by card" in page
    token = re.search(r"t=([^&\"]+)", back).group(1)
    r = client.post(f"/order/{data['order_id']}/pay?t={token}")
    assert r.status_code == 302 and r.headers["Location"].startswith("/dev/fake-pay/")


def webhook(client, order, secret=SECRET, amount=None, sign=True):
    body = json.dumps({"type": "payment.succeeded", "id": "evt_1", "payload": {
        "id": "p_1", "amount": order["total_cents"] if amount is None else amount, "currency": "ZAR",
        "status": "succeeded", "metadata": {"orderId": order["id"], "checkoutId": order["checkout_id"]}}}).encode()
    ts = str(int(time.time()))
    headers = {"webhook-id": "msg_1", "webhook-timestamp": ts,
               "webhook-signature": sign_for_tests(secret, "msg_1", ts, body) if sign else "v1,bad"}
    return client.post("/webhook/yoco", data=body, headers=headers, content_type="application/json")


def test_signed_webhook_marks_paid(app):
    c = conn(app)
    with c:
        dbm.set_setting(c, "yoco_webhook_secret", SECRET)
    client = app.test_client()
    data = place(app, client).get_json()
    order = c.execute("SELECT * FROM orders WHERE id = ?", (data["order_id"],)).fetchone()

    assert webhook(client, order, sign=False).status_code == 401
    assert webhook(client, order, amount=1).status_code == 200          # wrong amount: ignored
    assert c.execute("SELECT payment_status FROM orders WHERE id = ?", (order["id"],)).fetchone()[0] == "unpaid"
    assert webhook(client, order).status_code == 200
    row = c.execute("SELECT payment_status, status FROM orders WHERE id = ?", (order["id"],)).fetchone()
    assert row["payment_status"] == "paid" and row["status"] == "new"


def test_connect_payment_notifications(app):
    admin = app.test_client()
    tok = login(admin)
    r = admin.post("/admin/yoco/connect", data={"csrf_token": tok})
    assert r.status_code == 302
    assert dbm.get_setting(conn(app), "yoco_webhook_secret").startswith("whsec_")
    assert b"Payment notifications: connected" in admin.get("/admin/settings").data


def test_quote_only_delivery_hidden_with_card_payments(app):
    client = app.test_client()
    page = client.get("/checkout").get_data(as_text=True)
    assert 'value="pudo"' in page and 'value="delivery"' not in page      # delivery has no fixed fee
    r = place(app, client, fulfilment="delivery", address="1 Long Street, Pretoria")
    assert r.status_code == 400


def test_pudo_locker_order_and_booking(app):
    client = app.test_client()
    lockers = client.get("/api/pudo/lockers").get_json()["lockers"]
    assert any(l[0] == "CG107" for l in lockers)
    r = place(app, client, fulfilment="pudo", locker="x", locker_code="cg107")
    assert r.status_code == 200, r.get_json()
    order = conn(app).execute("SELECT * FROM orders WHERE id = ?", (r.get_json()["order_id"],)).fetchone()
    assert order["pudo_terminal"] == "CG107" and "Engen Garsfontein" in order["address"]
    assert order["delivery_cents"] == 7000 and order["total_cents"] == 2 * 4000 + 7000

    r = place(app, client, fulfilment="pudo")
    assert r.status_code == 400 and "locker" in r.get_json()["fields"]

    admin = app.test_client()
    tok = login(admin)
    page = admin.get(f"/admin/orders/{order['id']}").get_data(as_text=True)
    assert "Book PUDO shipment" in page
    r = admin.post(f"/admin/orders/{order['id']}/pudo", data={"csrf_token": tok, "size": "S", "terminal": "CG107"})
    assert r.status_code == 302
    row = conn(app).execute("SELECT * FROM orders WHERE id = ?", (order["id"],)).fetchone()
    assert row["pudo_waybill"].startswith("PUDOD") and row["pudo_pin"] == "270089"
    page = admin.get(f"/admin/orders/{order['id']}").get_data(as_text=True)
    assert "270089" in page and "Waybill label" in page
    assert admin.get(f"/admin/orders/{order['id']}/pudo/label").headers["Location"].startswith("https://example.com/")
    rates = admin.get("/admin/api/pudo/rates").get_json()
    assert rates["rates"] and "CG" in rates["to"]


def test_without_keys_shop_still_works(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, pay=False, pudo=False)
    client = app.test_client()
    assert client.get("/api/pudo/lockers").get_json() == {"lockers": []}
    r = place(app, client, fulfilment="pudo", locker="Engen Garsfontein, Pretoria")
    assert r.status_code == 200 and "new=1" in r.get_json()["redirect"]
    order = conn(app).execute("SELECT * FROM orders WHERE id = ?", (r.get_json()["order_id"],)).fetchone()
    assert order["status"] == "new" and order["pudo_terminal"] is None
    assert b"Place order" in client.get("/checkout").data


def test_admin_picks_up_payments_without_webhook(app):
    """A customer pays and closes the tab: opening the admin asks Yoco and marks it paid."""
    client = app.test_client()
    data = place(app, client).get_json()
    checkout_id = conn(app).execute("SELECT checkout_id FROM orders WHERE id = ?", (data["order_id"],)).fetchone()[0]
    app.extensions["shop"].payments.set_status(checkout_id, "paid")      # paid at Yoco, never came back
    admin = app.test_client()
    login(admin)
    assert b"Yoco confirmed a card payment" in admin.get("/admin/").data
    row = conn(app).execute("SELECT status, payment_status FROM orders WHERE id = ?", (data["order_id"],)).fetchone()
    assert row["payment_status"] == "paid" and row["status"] == "new"
