"""Cortex va KSC klientlarini soxta HTTP server bilan tekshirish (sahifalash, autentifikatsiya, formatlar)."""

import json

import httpx
import pytest

from agentmon.config import Settings
from agentmon.engine.inventory import cortex, ksc


@pytest.fixture
def mock_http(monkeypatch):
    def install(handler):
        real = httpx.AsyncClient

        def factory(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install


async def test_cortex_paginates_and_authenticates(mock_http):
    total = 230
    seen_auth = []

    def handler(req: httpx.Request):
        seen_auth.append((req.headers.get("x-xdr-auth-id"), req.headers.get("authorization")))
        rd = json.loads(req.content)["request_data"]
        eps = [{"endpoint_name": f"PC-{i}", "endpoint_status": "CONNECTED", "operational_status": "PROTECTED",
                "ip": [f"10.0.{i // 250}.{i % 250 + 1}"], "last_seen": 1_700_000_000_000}
               for i in range(rd["search_from"], min(rd["search_to"], total))]
        return httpx.Response(200, json={"reply": {"total_count": total, "result_count": len(eps), "endpoints": eps}})

    mock_http(handler)
    s = Settings(_env_file=None, cortex_fqdn="api-x.xdr.eu.paloaltonetworks.com", cortex_key_id="7", cortex_key="secret")
    recs = await cortex.fetch(s)
    assert len(recs) == total and len(seen_auth) == 3
    assert seen_auth[0] == ("7", "secret")


async def test_cortex_advanced_key_hashes(mock_http):
    headers = {}

    def handler(req):
        headers.update(req.headers)
        return httpx.Response(200, json={"reply": {"total_count": 0, "endpoints": []}})

    mock_http(handler)
    s = Settings(_env_file=None, cortex_fqdn="x", cortex_key_id="7", cortex_key="secret", cortex_key_type="advanced")
    await cortex.fetch(s)
    assert headers["authorization"] != "secret" and len(headers["authorization"]) == 64
    assert len(headers["x-xdr-nonce"]) == 64


async def test_ksc_flow(mock_http):
    calls = []
    hosts = [{"type": "params", "value": {
        "KLHST_WKS_WINHOSTNAME": f"PC-{i}", "KLHST_WKS_STATUS": 0x1C, "KLHST_WKS_RTP_STATE": 4,
        "KLHST_WKS_IP_LONG": {"type": "long", "value": 0x0A000001 + i},
        "KLHST_WKS_LAST_VISIBLE": {"type": "datetime", "value": "2026-09-21T10:00:00Z"}}} for i in range(1200)]

    def handler(req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[1]
        calls.append(method)
        if method == "login":
            assert req.headers["authorization"].startswith("KSCBasic user=")
            return httpx.Response(200, headers={"set-cookie": "sess=1"})
        body = json.loads(req.content)
        if method == "HostGroup.FindHosts":
            assert "KLHST_WKS_RTP_STATE" in body["vecFieldsToReturn"]
            return httpx.Response(200, json={"strAccessor": "acc1", "PxgRetVal": 1200})
        if method == "ChunkAccessor.GetItemsCount":
            return httpx.Response(200, json={"PxgRetVal": 1200})
        if method == "ChunkAccessor.GetItemsChunk":
            st, n = body["nStart"], body["nCount"]
            return httpx.Response(200, json={"pChunk": {"KLCSP_ITERATOR_ARRAY": hosts[st:st + n]}, "PxgRetVal": n})
        if method == "ChunkAccessor.Release":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    mock_http(handler)
    s = Settings(_env_file=None, ksc_url="https://ksc:13299", ksc_user="u", ksc_password="p")
    recs = await ksc.fetch(s)
    assert len(recs) == 1200 and recs[0].ips[0].startswith("10.0.")
    assert calls.count("ChunkAccessor.GetItemsChunk") == 3 and calls[-1] == "ChunkAccessor.Release"


async def test_ksc_error_is_raised(mock_http):
    def handler(req):
        if req.url.path.endswith("login"):
            return httpx.Response(200)
        return httpx.Response(200, json={"PxgError": {"code": 1, "message": "Access denied"}})

    mock_http(handler)
    with pytest.raises(RuntimeError, match="Access denied"):
        await ksc.fetch(Settings(_env_file=None, ksc_url="https://ksc:13299", ksc_user="u", ksc_password="p"))


async def test_proxy_only_for_cloud_cortex_not_internal_ksc(monkeypatch):
    """Internetga proxy orqali chiqiladigan serverda: Cortex (bulut) — proxy orqali, KSC (ichki) — hech qachon."""
    import httpx

    from agentmon.config import Settings
    from agentmon.engine.inventory import cortex, ksc
    seen: dict[str, bool] = {}
    real = httpx.AsyncClient

    class Spy(real):
        def __init__(self, *a, **kw):
            seen["ksc" if "verify" in kw else "cortex"] = kw.get("trust_env", True)
            super().__init__(*a, transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setattr(httpx, "AsyncClient", Spy)
    monkeypatch.setattr("agentmon.engine.inventory.http.asyncio.sleep", lambda *_: _none())
    s = Settings(_env_file=None, cortex_fqdn="api-x.xdr.eu.paloaltonetworks.com", cortex_key="k",
                 ksc_url="https://172.25.25.111:13299", ksc_user="u")
    for fetch in (cortex.fetch, ksc.fetch):
        try:
            await fetch(s)
        except httpx.HTTPStatusError:
            pass
    assert seen == {"cortex": True, "ksc": False}


async def _none():
    return None
