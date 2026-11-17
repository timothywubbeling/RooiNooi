"""Rooi Nooi online shop (storefront + admin)."""
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, has_request_context, render_template, request
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth
from . import db as dbm
from .config import SEED_DIR, load_config
from .helpers import register_filters
from .payment import build_payments
from .pudo import build_pudo
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


def _git_commit():
    """Short id of the code this server runs, shown on /healthz so anyone can check that an
    update went live. None when the code is not a git checkout (e.g. AWS Lambda)."""
    git = Path(__file__).resolve().parent.parent / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7] or None                     # checked out at a single commit
        ref = head[5:]
        if (git / ref).is_file():
            return (git / ref).read_text().strip()[:7] or None
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        pass
    return None


def _over_https(app):
    """Is this visitor on HTTPS? Nginx says so in X-Forwarded-Proto (read by ProxyFix); an
    https SHOP_BASE_URL counts too, in case a proxy does not pass that header on."""
    base = str(app.extensions["shop"].cfg.get("base_url") or "")
    return request.is_secure or base.startswith("https://")


class _SessionInterface(SecureCookieSessionInterface):
    """Mark the admin login cookie Secure (HTTPS-only) whenever the shop is used over HTTPS, so
    a browser never sends it over plain http (e.g. on public Wi-Fi). Local http still works."""

    def get_cookie_secure(self, app):
        return super().get_cookie_secure(app) or (has_request_context() and _over_https(app))


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
    app.session_interface = _SessionInterface()
    # Nginx (or a Function URL / CDN) sits in front; trust its forwarding headers once.
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
        cfg=cfg, db=database, payments=build_payments(cfg), pudo=build_pudo(cfg),
        build=_static_build_id(), version=VERSION, commit=_git_commit(),
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
        if _over_https(app):
            # Browsers that have been here over HTTPS use only HTTPS for this domain for a year
            # (not subdomains). Browsers ignore this header on plain http responses.
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.path.startswith(("/admin", "/order/", "/api/")) and "Cache-Control" not in resp.headers:
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
