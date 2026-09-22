from datetime import datetime, timedelta, timezone

from agentmon.engine.hosts import build_hosts
from agentmon.engine.identity import Candidate, resolve, verify
from agentmon.engine.inventory import cortex, ksc
from agentmon.engine.inventory.ad import filetime
from agentmon.engine.inventory.adns import build_a_record, parse_a_record
from agentmon.model import ConsoleRecord, assign_keys, dedupe_latest, netbios_key, norm_host

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


def test_netbios_key():
    assert netbios_key("ACCOUNTING-LAPTOP-01.corp.local") == "accounting-lapt"
    assert netbios_key("CORP\\PC-0412$") == "pc-0412"
    assert netbios_key(None) is None


def test_assign_keys_truncates_to_netbios():
    out = assign_keys([rec("cortex", "accounting-laptop-01"), rec("cortex", "pc-0412")])
    assert sorted(r.name for r in out) == ["accounting-lapt", "pc-0412"]
    assert {r.display_name for r in out} == {"accounting-laptop-01", "pc-0412"}   # ko'rinadigan nom to'liq qoladi


def test_assign_keys_merges_short_and_full_name_of_same_machine():
    old = rec("ksc", "accounting-lapt", last_seen=NOW - timedelta(days=5), version="old")
    new = rec("ksc", "accounting-laptop-01", last_seen=NOW, version="new")
    out = assign_keys([old, new])
    assert [(r.name, r.version) for r in out] == [("accounting-lapt", "new")]


def test_assign_keys_keeps_colliding_long_names_apart():
    """Domensiz qurilmalar (masalan, macOS): 15 belgisi bir xil, lekin boshqa-boshqa kompyuterlar."""
    out = assign_keys([rec("cortex", "design-macbook-pro-1"), rec("cortex", "design-macbook-pro-2")])
    assert sorted(r.name for r in out) == ["design-macbook-pro-1", "design-macbook-pro-2"]


def test_long_hostname_joins_across_sources():
    """AD/KSC NetBIOS nomi (15 belgi) va Cortex to'liq nomi bitta hostga birlashadi."""
    ad = assign_keys([ConsoleRecord("ad", "accounting-lapt", "accounting-laptop-01", True,
                                    details={"enabled": True, "stale": False})])
    ks = assign_keys([ksc.to_record({"KLHST_WKS_WINHOSTNAME": "ACCOUNTING-LAPT", "KLHST_WKS_STATUS": 0x1C,
                                     "KLHST_WKS_RTP_STATE": 4}, NOW, timedelta(hours=72))])
    cx = assign_keys([cortex.to_record({"endpoint_name": "ACCOUNTING-LAPTOP-01", "endpoint_status": "CONNECTED"})])
    consoles = {p: {r.name: r for r in recs} for p, recs in (("ad", ad), ("ksc", ks), ("cortex", cx))}
    hosts = build_hosts(consoles, include_servers=False)
    assert list(hosts) == ["accounting-lapt"]
    assert hosts["accounting-lapt"].sources == ["ad", "cortex", "ksc"]
    assert hosts["accounting-lapt"].display_name == "ACCOUNTING-LAPTOP-01"


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


def _verify(res, sessions, signals):
    return verify(res, sessions.get, lambda ip, grp: signals.get((ip, grp)))


def test_verify_requires_in_session_dc_traffic_for_pre_session_dns():
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", now - 5 * 86400),
                   Candidate("10.0.0.6", "pc-b", "dns", now - 5 * 86400),
                   Candidate("10.0.0.7", "pc-c", "dns", now - 600),         # seans ichida ro'yxatdan o'tgan
                   Candidate("10.0.0.9", "pc-e", "dns", None)], now, 14 * 86400)
    sessions = {ip: now - 3600 for ip in ("10.0.0.5", "10.0.0.6", "10.0.0.7", "10.0.0.9")}
    signals = {("10.0.0.6", "ad-auth"): now - 60,
               ("10.0.0.9", "ad-auth"): now - 2 * 86400}                        # DC trafigi — oldingi seansda
    assert set(_verify(res, sessions, signals)) == {"10.0.0.6", "10.0.0.7"}


def test_verify_dns_from_yesterday_is_not_trusted_for_new_device():
    """Kecha pc-a shu IP'ni olgan (DNS yangi), bugun DHCP uni domensiz qurilmaga bergan."""
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", now - 20 * 3600)], now, 14 * 86400)
    assert _verify(res, {"10.0.0.5": now - 3600}, {("10.0.0.5", "ad-auth"): now - 19 * 3600}) == {}


def test_verify_dhcp_reuse_drops_stale_agent_evidence():
    """Laptop A kecha Cortex'ga shu IP bilan xabar bergan; bugun IP'ni agentsiz, domensiz B olgan."""
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "laptop-a", "cortex", now - 16 * 3600)], now, 14 * 86400)
    assert _verify(res, {"10.0.0.5": now - 3600}, {}) == {}


def test_verify_keeps_stale_agent_evidence_when_corroborated_in_session():
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "cortex", now - 16 * 3600),
                   Candidate("10.0.0.6", "pc-b", "ksc", now - 16 * 3600)], now, 14 * 86400)
    sessions = {"10.0.0.5": now - 3600, "10.0.0.6": now - 3600}
    signals = {("10.0.0.5", "ad-auth"): now - 3000,     # domen a'zosi: agent to'xtagan bo'lsa ham IP unga tegishli
               ("10.0.0.6", "si"): now - 60}            # SI trafigi agent dalilini tasdiqlamaydi
    assert set(_verify(res, sessions, signals)) == {"10.0.0.5"}


def test_verify_continuous_session_keeps_agent_evidence():
    """Kompyuter bir hafta uzluksiz yoniq, agent 3 kun oldin to'xtagan: IP boshqaga o'tmagan — moslash qoladi."""
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "cortex", now - 3 * 86400)], now, 14 * 86400)
    assert set(_verify(res, {"10.0.0.5": now - 7 * 86400}, {})) == {"10.0.0.5"}


def test_verify_ignores_inactive_ips():
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", None)], now, 14 * 86400)
    assert set(_verify(res, {}, {})) == {"10.0.0.5"}


def test_ad_computer_uses_samaccountname_as_key(monkeypatch):
    from agentmon.config import Settings
    from agentmon.engine.inventory.ad import ADClient
    entry = {"cn": [b"ACCOUNTING-LAPT"], "sAMAccountName": [b"ACCOUNTING-LAPT$"],
             "dNSHostName": [b"accounting-laptop-01.corp.local"], "userAccountControl": [b"4096"],
             "lastLogonTimestamp": [b"134000000000000000"]}
    client = ADClient(Settings(_env_file=None, ad_base_dn="DC=corp,DC=local"))
    monkeypatch.setattr(client, "_paged", lambda *a, **kw: iter([("CN=ACCOUNTING-LAPT,DC=corp,DC=local", entry)]))
    recs, dcs = client._computers(None, NOW)
    assert recs[0].name == "accounting-lapt" and recs[0].display_name == "accounting-laptop-01"
    assert recs[0].fqdn == "accounting-laptop-01.corp.local" and dcs == []


def test_verify_smb_to_dc_does_not_prove_domain_membership():
    """Domensiz qurilma NTLM bilan DC'dagi papkaga ulangan (445) — bu eski DNS dalilini tasdiqlamaydi."""
    now = 1_000_000
    res = resolve([Candidate("10.0.0.5", "pc-a", "dns", now - 5 * 86400)], now, 14 * 86400)
    assert _verify(res, {"10.0.0.5": now - 3600}, {("10.0.0.5", "ad"): now - 60}) == {}
