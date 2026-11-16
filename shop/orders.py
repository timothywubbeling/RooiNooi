"""Order payment helpers shared by the shop pages, the Yoco webhook and the admin."""
from datetime import datetime, timedelta, timezone

from flask import url_for

from . import db as dbm
from .helpers import public_base, services
from .payment import PAID, PaymentError


def get_order(conn, order_id):
    return conn.execute("SELECT * FROM orders WHERE id = ?", (str(order_id or "").upper(),)).fetchone()


def mark_paid(conn, order_id, how="yoco"):
    """Mark an order paid (once). A card order waiting for payment becomes a New order. True if it changed."""
    now = dbm.utcnow()
    with conn:
        changed = conn.execute(
            "UPDATE orders SET payment_status = 'paid', paid_at = ?, updated_at = ?, payment_method = ?, "
            "status = CASE WHEN status = 'pending' THEN 'new' ELSE status END "
            "WHERE id = ? AND payment_status != 'paid'",
            (now, now, how, order_id)).rowcount
    if changed:
        print(f">> order {order_id} paid ({how})")
    return bool(changed)


def order_link(order, **extra):
    """Absolute address of the customer's order page (Yoco needs full https links)."""
    path = url_for("store.order_page", order_id=order["id"], t=order["token"], **extra)
    return public_base() + path


def start_payment(conn, order):
    """Create a Yoco checkout for the order and return the address to send the customer to."""
    payments = services().payments
    checkout = payments.create_checkout(
        order,
        success_url=order_link(order, paid=1),
        cancel_url=order_link(order, cancelled=1),
        failure_url=order_link(order, failed=1),
    )
    with conn:
        conn.execute("UPDATE orders SET checkout_id = ?, updated_at = ? WHERE id = ?",
                     (checkout["id"], dbm.utcnow(), order["id"]))
    return checkout["redirect_url"]


def refresh_payment(conn, order):
    """Ask Yoco whether a waiting card order has been paid (for when the webhook is slow or missing)."""
    payments = services().payments
    if order["payment_status"] == "paid" or not order["checkout_id"] or not payments.online:
        return order
    if payments.checkout_status(order["checkout_id"]) == PAID:
        mark_paid(conn, order["id"])
        return get_order(conn, order["id"])
    return order


def refresh_recent_pending(conn, limit=6, days=3):
    """Ask Yoco about the latest card orders still waiting, so a payment is never missed even
    without webhooks (a customer who paid and closed the tab). Returns how many became paid."""
    payments = services().payments
    if not payments.online:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM orders WHERE status = 'pending' AND payment_status != 'paid' AND checkout_id IS NOT NULL "
        "AND created_at >= ? ORDER BY created_at DESC LIMIT ?", (since, limit)).fetchall()
    paid = 0
    for order in rows:
        if refresh_payment(conn, order)["payment_status"] == "paid":
            paid += 1
    return paid


__all__ = ["refresh_recent_pending", "get_order", "mark_paid", "order_link", "start_payment", "refresh_payment", "PaymentError"]
