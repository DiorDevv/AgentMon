"""API: autentifikatsiya oqimi va asosiy endpoint'lar (haqiqiy PostgreSQL bilan)."""

import httpx
import pytest

from agentmon.api import auth, queries
from agentmon.api.main import _csv_safe, app
from agentmon.config import get_settings

SECRET = "x" * 64


@pytest.fixture
async def client(dsn, monkeypatch):
    pw_hash = auth.hash_password("Maxfiy-123")
    for k, v in {"DATABASE_URL": dsn, "WEB_AUTH": "ldap", "WEB_SECRET": SECRET, "AD_SERVER": "",
                 "WEB_ADMIN_USER": "admin", "WEB_ADMIN_PASSWORD_HASH": pw_hash}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    auth._attempts.clear()
    queries.invalidate()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c
    get_settings.cache_clear()


async def test_requires_login(client):
    assert (await client.get("/api/summary")).status_code == 401
    assert (await client.get("/api/hosts")).status_code == 401


async def test_login_flow(client):
    r = await client.post("/api/login", json={"username": "admin", "password": "noto'g'ri"})
    assert r.status_code == 401
    r = await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert (await client.get("/api/me")).json()["user"] == "admin"
    assert (await client.get("/api/summary")).status_code == 200
    await client.post("/api/logout")
    client.cookies.clear()
    assert (await client.get("/api/me")).status_code == 401


async def test_empty_password_rejected(client):
    r = await client.post("/api/login", json={"username": "admin", "password": ""})
    assert r.status_code == 401


async def test_tampered_cookie_rejected(client):
    client.cookies.set(auth.COOKIE, "eyJ1IjoiYWRtaW4ifQ.fake.sig")
    assert (await client.get("/api/me")).status_code == 401


async def test_bruteforce_rate_limited(client):
    codes = [(await client.post("/api/login", json={"username": "admin", "password": f"p{i}"})).status_code
             for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


async def test_endpoints_on_empty_db(client):
    await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    for path in ("/api/summary", "/api/hosts", "/api/trend", "/api/unknown", "/api/system", "/api/sites",
                 "/api/unknown/ignored", "/api/hosts.csv"):
        r = await client.get(path)
        assert r.status_code == 200, path
    assert (await client.get("/api/hosts/999999")).status_code == 404
    assert (await client.get("/api/hosts?product=bogus")).status_code == 400
    assert (await client.post("/api/unknown/ignore", json={"ip": "not-ip"})).status_code == 400
    assert (await client.delete("/api/unknown/ignore/not-ip")).status_code == 400


def test_csv_formula_injection_neutralized():
    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("PC-01") == "PC-01"
    assert _csv_safe("") == ""


def test_password_hash_roundtrip():
    h = auth.hash_password("abc")
    assert auth.verify_password("abc", h) and not auth.verify_password("abd", h)
    assert not auth.verify_password("abc", "garbage")
    assert "$" not in h   # docker compose .env ichida '$' interpolatsiya qilinadi


def test_legacy_dollar_hash_still_accepted():
    assert auth.verify_password("abc", auth.hash_password("abc").replace(":", "$"))


def test_check_config_rejects_bad_setup(monkeypatch):
    from agentmon.config import Settings
    with pytest.raises(RuntimeError, match="WEB_SECRET"):
        auth.check_config(Settings(_env_file=None, web_auth="ldap", web_secret="short", ad_server="ldaps://x",
                                   ad_base_dn="DC=x"))
    with pytest.raises(RuntimeError, match="WEB_AUTH"):
        auth.check_config(Settings(_env_file=None, web_auth="kerberos"))
    auth.check_config(Settings(_env_file=None, web_auth="none"))
