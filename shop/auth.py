"""Admin login: hashed password, session cookie, CSRF token, login throttling."""
import functools
import hmac
import secrets

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db as dbm

DEFAULT_PASSWORD = "1234"


def ensure_password(conn):
    """Seed the admin password the first time the app runs. It must be changed at first login."""
    if dbm.get_setting(conn, "admin_password_hash") is None:
        dbm.set_setting(conn, "admin_password_hash", generate_password_hash(DEFAULT_PASSWORD))
        dbm.set_setting(conn, "admin_password_version", secrets.token_hex(8))
        dbm.set_setting(conn, "admin_password_must_change", "1")


def set_password(conn, password):
    dbm.set_setting(conn, "admin_password_hash", generate_password_hash(password))
    dbm.set_setting(conn, "admin_password_version", secrets.token_hex(8))
    dbm.set_setting(conn, "admin_password_must_change", "0")


def check_password(conn, password):
    stored = dbm.get_setting(conn, "admin_password_hash")
    return bool(stored) and check_password_hash(stored, password or "")


def _limiter():
    return current_app.extensions["shop"].login_limiter


def _client_key():
    return request.remote_addr or "?"


def locked_out():
    return _limiter().blocked(_client_key())


def record_failure():
    _limiter().record(_client_key())


def clear_failures():
    _limiter().clear(_client_key())


def log_in(conn):
    session.clear()
    session["admin"] = True
    session["pw"] = dbm.get_setting(conn, "admin_password_version")
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True


def is_logged_in():
    if not session.get("admin"):
        return False
    # Changing the password signs out every other phone.
    return hmac.compare_digest(str(session.get("pw", "")),
                               str(dbm.get_setting(g.db, "admin_password_version") or ""))


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Your session expired. Reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            if request.path.startswith("/admin/api/"):
                abort(401)
            return redirect(url_for("admin.login", next=request.full_path if request.query_string else request.path))
        if request.method == "POST":
            check_csrf()
        must_change = dbm.get_setting(g.db, "admin_password_must_change") == "1"
        if must_change and request.endpoint not in ("admin.change_password", "admin.logout"):
            return redirect(url_for("admin.change_password"))
        return view(*args, **kwargs)
    return wrapped
