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
                 "WEB_ADMIN_USER": "admin", "WEB_ADMIN_PASSWORD_HASH": pw_hash,
                 "WEB_COOKIE_SECURE": "false"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    queries.invalidate()
    async with app.router.lifespan_context(app):
        await app.state.pool.execute("TRUNCATE login_failure")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.0.0.66", 5000)),
                                     base_url="http://t") as c:
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
    # To'g'ri parol ham bloklangan davrda qabul qilinmaydi (aks holda cheklov ma'nosiz).
    assert (await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})).status_code == 429


async def test_attacker_cannot_lock_out_admin_from_another_ip(client):
    for i in range(6):
        await client.post("/api/login", json={"username": "admin", "password": f"p{i}"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.0.0.7", 5000)),
                                 base_url="http://t") as admin:
        r = await admin.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    assert r.status_code == 200


async def test_username_spraying_is_limited_per_ip(client):
    codes = [(await client.post("/api/login", json={"username": f"user{i}", "password": "x"})).status_code
             for i in range(22)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


async def test_successful_login_resets_failures(client):
    for i in range(4):
        await client.post("/api/login", json={"username": "admin", "password": f"p{i}"})
    assert (await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})).status_code == 200
    codes = [(await client.post("/api/login", json={"username": "admin", "password": f"q{i}"})).status_code
             for i in range(5)]
    assert codes == [401] * 5


async def test_endpoints_on_empty_db(client):
    await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    for path in ("/api/summary", "/api/hosts", "/api/trend", "/api/unknown", "/api/system", "/api/sites",
                 "/api/unknown/ignored", "/api/hosts.csv", "/api/audit"):
        r = await client.get(path)
        assert r.status_code == 200, path
    u = (await client.get("/api/unknown")).json()
    assert set(u) == {"items", "dc_known"} and isinstance(u["items"], list)
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


async def test_only_enabled_products_returned(dsn, monkeypatch, client):
    monkeypatch.setenv("PRODUCTS_ENABLED", "cortex,ksc,si")
    get_settings.cache_clear()
    queries.invalidate()
    await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    me = (await client.get("/api/me")).json()
    assert me["products"] == ["cortex", "ksc", "si"]
    s = (await client.get("/api/summary")).json()
    assert set(s["products"]) == {"cortex", "ksc", "si"}
    assert (await client.get("/api/hosts?product=ad")).status_code == 400
    header = (await client.get("/api/hosts.csv")).text.splitlines()[0]
    assert "AD" not in header.split(";") and "Kaspersky" in header
    sysinfo = (await client.get("/api/system")).json()
    assert set(sysinfo["config"]["thresholds"]) == {"cortex", "ksc", "si"}


def test_products_setting_validation():
    from agentmon.config import Settings
    assert Settings(_env_file=None, products_enabled="SI, cortex").products == ("cortex", "si")
    with pytest.raises(ValueError, match="noma'lum"):
        Settings(_env_file=None, products_enabled="cortex,edr").products
    with pytest.raises(ValueError, match="bo'sh"):
        Settings(_env_file=None, products_enabled=" , ").products


async def test_unknown_device_shows_previous_owner_hint(client):
    """Moslash rad etilgan IP'da operator oldingi egasini (agent xabar bergan kompyuter) ko'radi."""
    await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})
    pool = app.state.pool
    await pool.execute("""INSERT INTO console_endpoint (product, name, display_name, ips, last_seen, healthy, synced_at)
                          VALUES ('cortex', 'laptop-a', 'laptop-a', '{10.99.0.5}', now() - interval '16 hours', true, now())""")
    await pool.execute("""INSERT INTO ip_presence (ip, site, first_seen, alive_since, last_seen)
                          VALUES ('10.99.0.5', 'Markaz', now() - interval '1 day', now() - interval '1 hour', now())""")
    try:
        items = {d["ip"]: d for d in (await client.get("/api/unknown")).json()["items"]}
        assert items["10.99.0.5"]["prev_owner"] == "LAPTOP-A"
    finally:
        await pool.execute("DELETE FROM console_endpoint WHERE name = 'laptop-a'")
        await pool.execute("DELETE FROM ip_presence WHERE ip = '10.99.0.5'")


# ------------------------------------------------------------------ LDAP login ("avval qidirish, keyin bind")
def _mock_ad(monkeypatch, users: dict[str, tuple], group: str = "", admin_group: str = ""):
    """Soxta AD: users = {dn: (sAMAccountName, parol[, guruhlar])}. Servis hisobi: CN=svc,DC=corp,DC=local / svc-pw.
    Mock ichma-ich guruh qoidasini bilmaydi — u oddiy `memberOfMock` atributiga almashtiriladi."""
    from ldap3 import MOCK_SYNC, Connection, Server
    from agentmon.config import Settings
    server = Server("mock-dc")
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry("CN=svc,DC=corp,DC=local", {"userPassword": "svc-pw", "sAMAccountName": "svc"})
    for dn, (sam, pw, *groups) in users.items():
        seed.strategy.add_entry(dn, {"userPassword": pw, "sAMAccountName": sam, "objectClass": ["person", "user"],
                                     "memberOfMock": list(groups[0]) if groups else [],
                                     "objectCategory": "person",
                                     "userPrincipalName": f"{sam}@{'corp.local' if 'DC=corp' in dn else 'other.local'}"})
    filters: list[str] = []

    def conn(_s, user, password):
        c = Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)
        real_search = c.search

        def search(base, flt, **kw):
            filters.append(flt)
            return real_search(base, flt.replace(auth._NESTED_MEMBER, "memberOfMock="), **kw)
        c.search = search
        return c

    monkeypatch.setattr(auth, "_connection", conn)
    s = Settings(_env_file=None, ad_server="ldaps://mock", ad_base_dn="DC=corp,DC=local",
                 ad_user="CN=svc,DC=corp,DC=local", ad_password="svc-pw", web_allowed_group=group,
                 web_admin_group=admin_group)
    return s, filters


def test_ldap_login_success_and_wrong_password(monkeypatch):
    s, _ = _mock_ad(monkeypatch, {"CN=John,OU=Users,DC=corp,DC=local": ("john", "J-pass")})
    assert auth._ldap_check(s, "john", "J-pass") == auth.Session("john", auth.ADMIN)   # admin guruhi berilmagan
    assert auth._ldap_check(s, "CORP\\john", "J-pass").user == "john"
    assert auth._ldap_check(s, "john@corp.local", "J-pass").user == "john"
    assert auth._ldap_check(s, "john", "wrong") is None
    assert auth._ldap_check(s, "nobody", "J-pass") is None


def test_ldap_other_domain_namesake_cannot_borrow_rights(monkeypatch):
    """Ishonchli domendagi OTHER\\john o'z paroli bilan CORP\\john (guruh a'zosi) huquqini ololmaydi."""
    s, _ = _mock_ad(monkeypatch, {"CN=John,OU=Users,DC=corp,DC=local": ("john", "corp-pass"),
                                  "CN=John,OU=Users,DC=other,DC=local": ("john", "other-pass")})
    assert auth._ldap_check(s, "OTHER\\john", "other-pass") is None
    assert auth._ldap_check(s, "john@other.local", "other-pass") is None


def test_ldap_group_filter_and_escaping(monkeypatch):
    s, filters = _mock_ad(monkeypatch, {"CN=John,OU=Users,DC=corp,DC=local": ("john", "J-pass")},
                          group="CN=AgentMon-Users,OU=Groups,DC=corp,DC=local")
    auth._ldap_check(s, "jo*hn)(x=1", "pw")
    assert filters and all("memberOf:1.2.840.113556.1.4.1941:=CN=AgentMon-Users" in f for f in filters)
    assert "jo\\2ahn\\29\\28x=1" in filters[0]                     # LDAP injection zararsizlantirildi


def test_ldap_requires_service_account():
    from agentmon.config import Settings
    with pytest.raises(RuntimeError, match="AD_USER"):
        auth.check_config(Settings(_env_file=None, web_auth="ldap", web_secret=SECRET, ad_server="ldaps://x",
                                   ad_base_dn="DC=x"))


def test_ldap_roles_from_groups(monkeypatch):
    users = "CN=AgentMon-Users,DC=corp,DC=local"
    admins = "CN=AgentMon-Admins,DC=corp,DC=local"
    s, _ = _mock_ad(monkeypatch, {"CN=Ann,DC=corp,DC=local": ("ann", "a", [users, admins]),
                                  "CN=Bob,DC=corp,DC=local": ("bob", "b", [users]),
                                  "CN=Eve,DC=corp,DC=local": ("eve", "e", [])},
                    group=users, admin_group=admins)
    assert auth._ldap_check(s, "ann", "a") == auth.Session("ann", auth.ADMIN)
    assert auth._ldap_check(s, "bob", "b") == auth.Session("bob", auth.VIEWER)
    assert auth._ldap_check(s, "eve", "e") is None                    # ruxsat guruhida emas


async def test_viewer_cannot_modify_and_admin_actions_are_audited(client):
    from agentmon.config import get_settings as gs
    viewer = auth.issue_cookie(gs(), auth.Session("bob", auth.VIEWER))
    pool = app.state.pool
    hid = await pool.fetchval("INSERT INTO host (name, display_name, sources) VALUES ('aud-pc', 'AUD-PC', '{ad}') "
                              "ON CONFLICT (name) DO UPDATE SET excluded = false RETURNING id")
    try:
        client.cookies.set(auth.COOKIE, viewer)
        assert (await client.get("/api/me")).json()["role"] == "viewer"
        assert (await client.post(f"/api/hosts/{hid}/exclude", json={"excluded": True, "note": "x"})).status_code == 403
        assert (await client.post("/api/unknown/ignore", json={"ip": "10.1.1.1"})).status_code == 403
        assert (await client.delete("/api/unknown/ignore/10.1.1.1")).status_code == 403
        assert (await client.get("/api/audit")).status_code == 200    # jurnalni hamma ko'radi

        client.cookies.clear()
        assert (await client.post("/api/login", json={"username": "admin", "password": "Maxfiy-123"})).json()["role"] == "admin"
        assert (await client.post(f"/api/hosts/{hid}/exclude", json={"excluded": True, "note": "test stend"})).status_code == 200
        assert (await client.post("/api/unknown/ignore", json={"ip": "10.1.1.1", "note": "printer"})).status_code == 200
        log = (await client.get("/api/audit")).json()
        actions = [(e["username"], e["action"], e["target"]) for e in log[:3]]
        assert actions == [("admin", "ip.ignore", "10.1.1.1"), ("admin", "host.exclude", "AUD-PC"), ("admin", "login", None)]
        assert log[1]["details"]["note"] == "test stend" and log[1]["ip"] == "10.0.0.66"
    finally:
        await pool.execute("DELETE FROM host WHERE id = $1", hid)
        await pool.execute("DELETE FROM ip_ignore WHERE ip = '10.1.1.1'")


async def test_legacy_cookie_without_role_is_viewer(client):
    from agentmon.config import get_settings as gs
    client.cookies.set(auth.COOKIE, auth._serializer(gs()).dumps({"u": "old"}))
    assert (await client.get("/api/me")).json()["role"] == "viewer"


def test_sample_secret_rejected_and_tls_settings(tmp_path):
    import subprocess
    from agentmon.config import KNOWN_SAMPLE_SECRETS, Settings, ksc_verify, ldap_tls
    sample = next(iter(x for x in KNOWN_SAMPLE_SECRETS if len(x) >= 32))
    with pytest.raises(RuntimeError, match="namunadan"):
        auth.check_config(Settings(_env_file=None, web_auth="ldap", web_secret=sample, web_admin_user="a",
                                   web_admin_password_hash="h"))
    ca = tmp_path / "corp-ca.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=ca",
                    "-keyout", str(tmp_path / "k"), "-out", str(ca)], check=True, capture_output=True)
    assert ldap_tls(Settings(_env_file=None, ad_verify_tls=True, ad_ca_file=str(ca))).ca_certs_file == str(ca)
    with pytest.raises(RuntimeError, match="AD_CA_FILE topilmadi"):
        ldap_tls(Settings(_env_file=None, ad_verify_tls=True, ad_ca_file="/yoq/ca.pem"))
    assert ksc_verify(Settings(_env_file=None)) is False
    assert ksc_verify(Settings(_env_file=None, ksc_verify_tls=True, ksc_ca_file="/app/ca/ksc.pem")) == "/app/ca/ksc.pem"
    assert Settings(_env_file=None).web_cookie_secure is True
