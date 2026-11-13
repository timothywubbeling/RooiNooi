"""Rooi Nooi online shop (storefront + admin)."""
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth
from . import db as dbm
from .config import SEED_DIR, load_config
from .helpers import register_filters
from .payment import ManualPayments
from .ratelimit import RateLimiter
from .seed import seed_if_empty

VERSION = "1.1.0"


def _static_build_id():
    """Changes whenever a CSS/JS file changes, so browsers fetch the new one."""
    digest = hashlib.sha1(VERSION.encode())
    static = Path(__file__).parent / "static"
    for path in sorted(static.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def create_app(cfg=None):
    cfg = cfg or load_config()
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="rooinooi_session",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),   # static files carry ?v=<build>
    )
    app.json.sort_keys = False
    # A tunnel / Function URL / CDN sits in front; trust its forwarding headers once.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    if cfg["database_url"]:
        database = dbm.Database(url=cfg["database_url"])
    else:
        cfg["data_dir"].mkdir(parents=True, exist_ok=True)
        database = dbm.Database(path=cfg["db_path"])
    database.init()

    conn = database.connect()
    try:
        with conn:
            database.lock(conn)
            auth.ensure_password(conn)
            seed_if_empty(conn, SEED_DIR)
            secret = cfg["flask_secret"] or dbm.get_setting(conn, "flask_secret")
            if not secret:
                # Session cookies are signed with this, so it is made once and kept.
                secret = secrets.token_hex(32)
                dbm.set_setting(conn, "flask_secret", secret)
    finally:
        conn.close()
    app.config["SECRET_KEY"] = secret

    app.extensions["shop"] = SimpleNamespace(
        cfg=cfg, db=database, payments=ManualPayments(),
        build=_static_build_id(), version=VERSION,
        login_limiter=RateLimiter(5, 600),     # 5 wrong passwords -> 10 minute lockout
        order_limiter=RateLimiter(8, 600),     # 8 orders per 10 minutes per address
        quote_limiter=RateLimiter(300, 60),
    )

    @app.before_request
    def open_db():
        g.db = database.connect()

    @app.teardown_request
    def close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.path.startswith(("/admin", "/order/", "/api/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def wants_json():
        return request.path.startswith(("/api/", "/admin/api/"))

    @app.errorhandler(404)
    def not_found(exc):
        if wants_json():
            return {"error": "Not found"}, 404
        return render_template("error.html", code=404, message="We couldn't find that page."), 404

    @app.errorhandler(400)
    def bad_request(exc):
        message = getattr(exc, "description", None) or "Bad request."
        if wants_json() or request.is_json:
            return {"error": message}, 400
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(401)
    def unauthorised(exc):
        return {"error": "Please log in again."}, 401

    @app.errorhandler(413)
    def too_large(exc):
        message = "Those photos are too big together. Add a few at a time."
        if wants_json():
            return {"error": message}, 413
        return render_template("error.html", code=413, message=message), 413

    register_filters(app)

    from .store import bp as store_bp
    from .admin import bp as admin_bp
    app.register_blueprint(store_bp)
    app.register_blueprint(admin_bp)
    return app
