from datetime import datetime, timedelta, timezone

from agentmon.engine.hosts import build_hosts
from agentmon.engine.identity import Candidate, resolve, verify
from agentmon.engine.inventory import cortex, ksc
from agentmon.engine.inventory.ad import filetime
from agentmon.engine.inventory.adns import build_a_record, parse_a_record
from agentmon.model import ConsoleRecord, dedupe_latest, norm_host

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def test_norm_host():
    assert norm_host("CORP\\PC-0412$") == "pc-0412"
    assert norm_host("PC-0412.corp.local") == "pc-0412"
    assert norm_host("  ") is None
    assert norm_host(None) is None


def test_dns_record_roundtrip():
    ts = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    assert parse_a_record(build_a_record("10.10.1.5", ts)) == ("10.10.1.5", ts)
    assert parse_a_record(build_a_record("10.10.1.6", None)) == ("10.10.1.6", None)
    assert parse_a_record(b"\x00" * 10) is None


def test_dns_record_non_a_ignored():
    blob = bytearray(build_a_record("10.0.0.1", None))
    blob[2] = 28  # AAAA
    assert parse_a_record(bytes(blob)) is None


def test_filetime():
    assert filetime(b"0") is None
    assert filetime(b"9223372036854775807") is None
    assert filetime(b"116444736000000000") == datetime(1970, 1, 1, tzinfo=UTC)


def test_cortex_record_mapping():
    r = cortex.to_record({
        "endpoint_name": "PC-0412", "endpoint_status": "CONNECTED", "operational_status": "PARTIALLY_PROTECTED",
        "ip": ["10.10.1.5", "fe80::1"], "last_seen": int(NOW.timestamp() * 1000), "agent_version": "8.5",
    })
    assert r.name == "pc-0412" and not r.healthy and r.ips == ["10.10.1.5"] and r.last_seen == NOW
    assert cortex.to_record({"endpoint_name": "X", "endpoint_status": "UNINSTALLED"}) is None


def test_ksc_record_mapping():
    base = {
        "KLHST_WKS_WINHOSTNAME": "PC-0412", "KLHST_WKS_IP_LONG": {"type": "long", "value": 0x0A0A0105},
        "KLHST_WKS_STATUS": 0x04 | 0x08 | 0x10, "KLHST_WKS_RTP_STATE": 4,
        "KLHST_WKS_LAST_VISIBLE": {"type": "datetime", "value": "2026-09-21T11:00:00Z"},
        "KLHST_WKS_LAST_UPDATE": {"type": "datetime", "value": "2026-09-21T08:00:00Z"},
    }
    r = ksc.to_record(base, NOW, timedelta(hours=72))
    assert r.healthy and r.ips == ["10.10.1.5"] and r.last_seen == datetime(2026, 9, 21, 11, tzinfo=UTC)

    assert ksc.to_record({**base, "KLHST_WKS_STATUS": 0x01}, NOW, timedelta(hours=72)) is None
    assert "KES" in ksc.to_record({**base, "KLHST_WKS_STATUS": 0x04}, NOW, timedelta(hours=72)).reason
    assert "to'xtatilgan" in ksc.to_record({**base, "KLHST_WKS_RTP_STATE": 1}, NOW, timedelta(hours=72)).reason
    old = {**base, "KLHST_WKS_LAST_UPDATE": "2026-09-10T00:00:00Z"}
    assert "eskirgan" in ksc.to_record(old, NOW, timedelta(hours=72)).reason


def test_ksc_ip_byte_order_heuristic():
    # 10.10.1.5 teskari tartibda saqlangan bo'lsa ham to'g'ri o'qiladi
    assert ksc._ip(0x05010A0A) == "10.10.1.5"


def rec(product, name, **kw):
    return ConsoleRecord(product=product, name=name, display_name=name, healthy=True, **kw)


def test_dedupe_keeps_latest():
    a = rec("cortex", "pc1", last_seen=NOW - timedelta(days=3), version="old")
    b = rec("cortex", "pc1", last_seen=NOW, version="new")
    assert [r.version for r in dedupe_latest([a, b])] == ["new"]


def test_build_hosts_rules():
    consoles = {
        "ad": {
            "pc1": rec("ad", "pc1", os="Windows 11", details={"enabled": True, "stale": False}),
            "old": rec("ad", "old", details={"enabled": True, "stale": True}),
            "srv1": rec("ad", "srv1", os="Windows Server 2022", details={"enabled": True, "stale": False}),
        },
        "cortex": {"lap9": rec("cortex", "lap9"), "old": rec("cortex", "old")},
        "ksc": {"pc1": rec("ksc", "pc1")},
    }
    hosts = build_hosts(consoles, include_servers=False)
    assert set(hosts) == {"pc1", "lap9", "old"}      # stale AD, lekin Cortex'da bor
    assert hosts["pc1"].sources == ["ad", "ksc"]
    assert "srv1" in build_hosts(consoles, include_servers=True)


def test_identity_newest_evidence_wins():
    now = 1_000_000
    cands = [
        Candidate("10.0.0.5", "pc-a", "dns", now - 86400),
        Candidate("10.0.0.5", "pc-b", "cortex", now - 60),
        Candidate("10.0.0.6", "pc-c", "dns", None),                 # statik
        Candidate("10.0.0.7", "pc-d", "ksc", now - 30 * 86400),     # juda eski
    ]
    res = resolve(cands, now, max_age=14 * 86400)
    assert res["10.0.0.5"].name == "pc-b" and res["10.0.0.5"].alternatives == 1
    assert res["10.0.0.6"].name == "pc-c"
    assert "10.0.0.7" not in res


def test_identity_tie_prefers_agent_over_dns():
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", 100), Candidate("10.0.0.5", "pc-b", "ksc", 100)], 200, 1000)
    assert res["10.0.0.5"].name == "pc-b"


def test_identity_agent_supersedes_stale_dns_of_same_host():
    now = 1_000_000
    cands = [
        Candidate("10.0.0.5", "pc-a", "dns", now - 5 * 86400),   # eski yozuv: pc-a oldin shu IP'da edi
        Candidate("10.0.0.9", "pc-a", "cortex", now - 60),        # agent: pc-a hozir 10.0.0.9 da
    ]
    res = resolve(cands, now, max_age=14 * 86400)
    assert "10.0.0.5" not in res and res["10.0.0.9"].name == "pc-a"


def test_identity_keeps_multiple_current_agent_ips():
    now = 1_000_000
    cands = [Candidate("10.0.0.5", "pc-a", "cortex", now - 60), Candidate("10.0.1.5", "pc-a", "cortex", now - 60),
             Candidate("10.0.1.5", "pc-a", "dns", now - 3 * 86400)]
    res = resolve(cands, now, max_age=14 * 86400)
    assert set(res) == {"10.0.0.5", "10.0.1.5"}


def test_verify_drops_old_dns_without_dc_traffic():
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", now - 5 * 86400),
                   Candidate("10.0.0.6", "pc-b", "dns", now - 5 * 86400),
                   Candidate("10.0.0.7", "pc-c", "dns", now - 3600),
                   Candidate("10.0.0.8", "pc-d", "cortex", now - 5 * 86400)], now, 14 * 86400)
    ok = verify(res, now, 2 * 86400, has_dc_traffic=lambda ip: ip == "10.0.0.6")
    assert set(ok) == {"10.0.0.6", "10.0.0.7", "10.0.0.8"}
