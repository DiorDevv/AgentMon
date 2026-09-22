import orjson

from agentmon.config import Settings
from agentmon.engine.classify import UNKNOWN_SITE, Classifier
from agentmon.engine.signals import ANY, SignalStore

S = Settings(
    _env_file=None,
    target_cortex="172.25.44.50:8888",
    target_ksc="172.25.25.111:13000,172.25.25.111:14000",
    target_si="172.25.43.205:8090",
    dc_ips="172.25.10.10",
    user_subnets="10.10.0.0/16=Markaz,10.10.5.0/24=Markaz-WiFi,10.20.0.0/16=Filial-1",
    exclude_subnets="10.10.99.0/24",
    exclude_ips="10.10.1.250",
)


def clf():
    return Classifier.from_settings(S, ad_dc_ips={"172.25.10.11"}, ad_subnets=[("10.30.0.0/16", "Filial-2")])


def raw(src, dst, dport, ev=1, ts=1000):
    return orjson.dumps({"src": src, "dst": dst, "dport": dport, "ev": ev, "ts": ts})


def test_target_groups():
    c = clf()
    assert c.target_group("172.25.44.50", 8888) == "cortex"
    assert c.target_group("172.25.25.111", 14000) == "ksc"
    assert c.target_group("172.25.43.205", 8090) == "si"
    assert c.target_group("172.25.10.10", 88) == "ad"
    assert c.target_group("172.25.10.11", 445) == "ad"      # AD'dan kelgan DC
    assert c.target_group("172.25.10.10", 53) is None       # DNS domen a'zoligini isbotlamaydi
    assert c.target_group("8.8.8.8", 443) is None


def test_site_longest_prefix_and_exclusions():
    c = clf()
    assert c.site_of("10.10.5.7") == "Markaz-WiFi"
    assert c.site_of("10.10.6.7") == "Markaz"
    assert c.site_of("10.30.1.1") == "Filial-2"
    assert c.site_of("10.10.99.5") is None      # exclude subnet
    assert c.site_of("10.10.1.250") is None     # exclude ip
    assert c.site_of("172.25.44.50") is None    # server
    assert c.site_of("192.168.1.1") is None     # user subnet emas
    assert c.site_of("not-an-ip") is None


def test_no_subnets_falls_back_to_private_ranges():
    c = Classifier.from_settings(Settings(_env_file=None))
    assert c.site_of("192.168.1.5") == UNKNOWN_SITE
    assert c.site_of("8.8.8.8") is None


def test_store_tracks_presence_and_groups():
    c, st = clf(), SignalStore(alive_gap=600)
    st.process_raw(raw("10.10.6.7", "8.8.8.8", 443, ts=1000), c, now=5000)
    st.process_raw(raw("10.10.6.7", "172.25.44.50", 8888, ts=1100), c, now=5000)
    assert st.last_seen("10.10.6.7", ANY) == 1100
    assert st.last_seen("10.10.6.7", "cortex") == 1100
    assert st.last_seen("10.10.6.7", "ksc") is None
    assert st.stats.accepted == 2 and st.stats.watermark == 1100


def test_denied_counts_as_presence_only():
    c, st = clf(), SignalStore(alive_gap=600)
    st.process_raw(raw("10.10.6.7", "172.25.44.50", 8888, ev=3, ts=1000), c, now=5000)
    assert st.last_seen("10.10.6.7", ANY) == 1000
    assert st.last_seen("10.10.6.7", "cortex") is None


def test_alive_session_restarts_after_gap():
    st = SignalStore(alive_gap=600)
    st.observe("1.1.1.1", "s", None, 1000)
    st.observe("1.1.1.1", "s", None, 1500)
    assert st.presence["1.1.1.1"].alive_since == 1000
    st.observe("1.1.1.1", "s", None, 3000)
    assert st.presence["1.1.1.1"].alive_since == 3000


def test_out_of_order_and_future_events():
    c, st = clf(), SignalStore(alive_gap=600)
    st.process_raw(raw("10.10.6.7", "172.25.44.50", 8888, ts=2000), c, now=5000)
    st.process_raw(raw("10.10.6.7", "172.25.44.50", 8888, ts=1500), c, now=5000)
    assert st.last_seen("10.10.6.7", "cortex") == 2000
    st.process_raw(raw("10.10.6.7", "172.25.44.50", 8888, ts=99999), c, now=5000)
    assert st.last_seen("10.10.6.7", "cortex") == 5000   # kelajak vaqti kesiladi


def test_malformed_and_ignored_sources():
    c, st = clf(), SignalStore(alive_gap=600)
    st.process_raw(b"{not json", c, now=5000)
    st.process_raw(orjson.dumps({"src": "10.10.6.7"}), c, now=5000)
    st.process_raw(raw("203.0.113.9", "10.10.6.7", 3389), c, now=5000)   # internetdan kiruvchi
    assert st.stats.malformed == 2 and st.stats.accepted == 0 and not st.presence


def test_dirty_tracking_roundtrip():
    st = SignalStore(alive_gap=600)
    st.observe("1.1.1.1", "s", "ksc", 1000)
    pres, sig = st.take_dirty()
    assert [ip for ip, _ in pres] == ["1.1.1.1"] and sig == [("1.1.1.1", "ksc", 1000)]
    assert st.take_dirty() == ([], [])
    st.restore_dirty(pres, sig)
    pres2, sig2 = st.take_dirty()
    assert len(pres2) == 1 and sig2 == sig


def test_denied_to_target_recorded_separately():
    c, st = clf(), SignalStore(alive_gap=600)
    st.process_raw(raw("10.10.6.7", "172.25.25.111", 13000, ev=3, ts=1000), c, now=5000)
    assert st.last_seen("10.10.6.7", "ksc!") == 1000 and st.last_seen("10.10.6.7", "ksc") is None


def test_prune_removes_old_entries():
    st = SignalStore(alive_gap=600)
    st.observe("1.1.1.1", "s", "ksc", 100)
    st.observe("2.2.2.2", "s", "ksc", 5000)
    assert st.prune(before=1000) == 1
    assert "1.1.1.1" not in st.presence and ("1.1.1.1", "ksc") not in st.last
    assert "2.2.2.2" in st.presence


def test_exporter_whitelist_and_tracking():
    st = SignalStore(600, frozenset({"172.25.0.1", "172.25.0.2"}))
    c = clf()
    ok = orjson.dumps({"src": "10.10.1.5", "dst": "172.25.44.50", "dport": 8888, "ev": 1, "ts": 1000, "exp": "172.25.0.1"})
    later = orjson.dumps({"src": "10.10.1.5", "dst": "8.8.8.8", "dport": 443, "ev": 1, "ts": 1010, "exp": "172.25.0.2"})
    spoofed = orjson.dumps({"src": "10.10.1.9", "dst": "172.25.44.50", "dport": 8888, "ev": 1, "ts": 1020,
                            "exp": "10.10.1.66"})
    for r in (ok, later, spoofed):
        st.process_raw(r, c, 2000)
    assert st.stats.rejected == 1 and "10.10.1.9" not in st.presence       # soxta eksporter e'tiborsiz
    assert st.presence["10.10.1.5"].exporter == "172.25.0.2"               # eng yangi hodisa eksporteri
    assert st.stats.exporters["172.25.0.1"].last_rx == 1000
    assert st.stats.exporters["172.25.0.2"].received == 1


def test_no_whitelist_accepts_any_exporter():
    st = SignalStore(600)
    st.process_raw(raw("10.10.1.5", "172.25.44.50", 8888), clf(), 2000)
    assert st.stats.rejected == 0 and "10.10.1.5" in st.presence


def test_dc_auth_ports_recorded_separately():
    st = SignalStore(600)
    c = clf()
    st.process_raw(raw("10.10.1.5", "172.25.10.10", 445, ts=1000), c, 2000)
    assert st.last.get(("10.10.1.5", "ad")) == 1000 and ("10.10.1.5", "ad-auth") not in st.last
    st.process_raw(raw("10.10.1.5", "172.25.10.10", 88, ts=1100), c, 2000)
    assert st.last[("10.10.1.5", "ad-auth")] == 1100 and st.last[("10.10.1.5", "ad")] == 1100
