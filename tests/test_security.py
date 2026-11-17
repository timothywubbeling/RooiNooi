"""HTTPS-only admin login cookie and HSTS, switched on whenever the shop is used over HTTPS."""
import re

import pytest

HTTPS = {"X-Forwarded-Proto": "https"}      # what Nginx sends in front of the live shop


def make_app(tmp_path, monkeypatch, base_url=None):
    monkeypatch.setenv("SHOP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SHOP_FAKE_PAYMENTS", "0")
    monkeypatch.setenv("SHOP_FAKE_PUDO", "0")
    if base_url:
        monkeypatch.setenv("SHOP_BASE_URL", base_url)
    else:
        monkeypatch.delenv("SHOP_BASE_URL", raising=False)
    from shop import create_app
    return create_app()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


def login_cookie(client, **kw):
    r = client.post("/admin/login", data={"password": "1234"}, **kw)
    assert r.status_code == 302, "login should succeed"
    return next(h for h in r.headers.getlist("Set-Cookie") if h.startswith("rooinooi_session="))


def test_login_cookie_is_https_only_behind_nginx(app):
    cookie = login_cookie(app.test_client(), headers=HTTPS)
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_hsts_sent_over_https_only(app):
    client = app.test_client()
    assert client.get("/", headers=HTTPS).headers.get("Strict-Transport-Security") == "max-age=31536000"
    assert "Strict-Transport-Security" not in client.get("/").headers


def test_local_http_still_works(app):
    """On a computer (http://localhost) the cookie can't be HTTPS-only, or nobody could log in."""
    client = app.test_client()
    assert "Secure" not in login_cookie(client)
    assert client.get("/admin/").status_code in (200, 302)


def test_https_shop_address_also_counts(tmp_path, monkeypatch):
    """If a proxy forgets X-Forwarded-Proto, an https SHOP_BASE_URL still switches both on."""
    app = make_app(tmp_path, monkeypatch, base_url="https://shop.example")
    client = app.test_client()
    assert "Secure" in login_cookie(client)
    assert client.get("/").headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_healthz_says_which_update_is_running(app):
    data = app.test_client().get("/healthz").get_json()
    assert data["ok"] is True
    assert data["commit"] is None or re.fullmatch(r"[0-9a-f]{7}", data["commit"])
