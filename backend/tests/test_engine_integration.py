"""Engine'ni haqiqiy PostgreSQL bilan sinash.

PostgreSQL manbai — conftest.py dagi `dsn` fixture'iga qarang.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from agentmon.config import Settings
from agentmon.engine.app import Engine
from agentmon.engine.identity import Candidate
from agentmon.engine.signals import Presence
from agentmon.model import NOT_INSTALLED, OK, ConsoleRecord

UTC = timezone.utc
TABLES = ("host_state_history", "host_state", "host_ip", "host", "console_endpoint", "dns_record", "net_subnet",
          "dc_server", "source_status", "ip_presence", "net_signal", "incident", "coverage_snapshot",
          "system_status", "ip_ignore")


def settings(dsn: str, **kw) -> Settings:
    base = dict(_env_file=None, database_url=dsn, redis_url="redis://127.0.0.1:1/0",
                user_subnets="10.10.0.0/16=Markaz", dc_ips="172.25.10.10", mass_outage_min=5)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
async def engine(dsn):
    # Baza engine ishga tushishidan OLDIN tozalanadi — aks holda engine oldingi testning ma'lumotini xotiraga yuklaydi.
    from agentmon import db
    pool = await db.create_pool(dsn, min_size=1, max_size=1)
    await db.migrate(pool)
    await pool.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    await pool.close()
    eng = Engine(settings(dsn))
    await eng.start()
    yield eng
    await eng.close()


N = 20


async def seed(eng: Engine, *, missing_cortex: set[int] = frozenset()) -> int:
    """N ta host: hammasi AD'da, Cortex'da (missing_cortex'dan tashqari), 5 soatdan beri tirik."""
    now = int(time.time())
    nowdt = datetime.fromtimestamp(now, UTC)
    ad, cx = [], []
    for i in range(N):
        name = f"pc-{i:02d}"
        ad.append(ConsoleRecord("ad", name, name, True, details={"enabled": True, "stale": False}))
        if i not in missing_cortex:
            cx.append(ConsoleRecord("cortex", name, name, True, ips=[f"10.10.0.{i + 1}"], last_seen=nowdt))
    await eng._replace_console("ad", ad)
    await eng._replace_console("cortex", cx)
    eng.source_ok.update(ad=now, cortex=now)
    eng.dns = [Candidate(f"10.10.0.{i + 1}", f"pc-{i:02d}", "dns", now - 600) for i in range(N)]
    await eng._rebuild_hosts()
    for i in range(N):
        ip = f"10.10.0.{i + 1}"
        eng.store.presence[ip] = Presence("Markaz", now - 86400, now - 5 * 3600, now - 10)
        eng.store.dirty_presence.add(ip)
        eng.store.observe(ip, "Markaz", "ad", now - 60)
        eng.store.observe(ip, "Markaz", "ad-auth", now - 60)
        if i not in missing_cortex:
            eng.store.observe(ip, "Markaz", "cortex", now - 30)
    eng.store.stats.last_rx_wall = time.time()
    eng.store.stats.watermark = now
    eng.data_since = time.time() - 3600
    return now


def cortex_state(eng: Engine, i: int) -> str | None:
    h = eng.hosts[f"pc-{i:02d}"]
    t = eng.tracked.get((h.id, "cortex"))
    return t.state if t else None


async def test_warmup_blocks_evaluation(engine):
    await seed(engine)
    engine.data_since = time.time()          # oqim hozirgina boshlandi
    await engine.evaluate_cycle()
    assert not engine.tracked
    assert await engine.pool.fetchval("SELECT count(*) FROM host_state") == 0


async def test_basic_states_are_persisted(engine):
    await seed(engine, missing_cortex={3})
    await engine.evaluate_cycle()
    assert cortex_state(engine, 0) == OK
    assert cortex_state(engine, 3) == NOT_INSTALLED
    row = await engine.pool.fetchrow(
        "SELECT s.state FROM host_state s JOIN host h ON h.id = s.host_id WHERE h.name = 'pc-03' AND product = 'cortex'")
    assert row["state"] == NOT_INSTALLED
    assert await engine.pool.fetchval("SELECT count(*) FROM host_state_history") == N * 4


async def test_single_host_failure_needs_confirmation(engine):
    now = await seed(engine)
    await engine.evaluate_cycle()
    engine.store.last[("10.10.0.5", "cortex")] = now - 7200   # bitta host jim bo'ldi
    engine.consoles["cortex"]["pc-04"].last_seen = datetime.fromtimestamp(now - 86400, UTC)  # konsol ham ko'rmayapti
    await engine.evaluate_cycle()
    assert cortex_state(engine, 4) == OK                      # 1-marta: hali tasdiqlanmagan
    await engine.evaluate_cycle()
    assert cortex_state(engine, 4) == "STOPPED"               # 2-marta: tasdiqlandi


async def test_mass_outage_is_held_and_reported(engine):
    now = await seed(engine)
    await engine.evaluate_cycle()
    for i in range(N):                                        # Broker VM "tushdi"
        engine.store.last[(f"10.10.0.{i + 1}", "cortex")] = now - 7200
    for _ in range(3):
        await engine.evaluate_cycle()
    assert all(cortex_state(engine, i) == OK for i in range(N))
    assert "cortex" in engine.outages
    inc = await engine.pool.fetchrow("SELECT * FROM incident WHERE kind = 'mass_outage'")
    assert inc["ended_at"] is None and inc["details"]["silent"] == N

    for i in range(N):                                        # tiklandi
        engine.store.last[(f"10.10.0.{i + 1}", "cortex")] = int(time.time())
    await engine.evaluate_cycle()
    assert "cortex" not in engine.outages
    assert await engine.pool.fetchval("SELECT ended_at FROM incident WHERE kind = 'mass_outage'") is not None


def _cortex_down(eng: Engine, now: int) -> None:
    """Broker VM tushdi: hech bir hostdan Cortex trafigi yo'q, konsol ham agentlarni ko'rmayapti."""
    for i in range(N):
        eng.store.last[(f"10.10.0.{i + 1}", "cortex")] = now - 7200
        eng.consoles["cortex"][f"pc-{i:02d}"].last_seen = datetime.fromtimestamp(now - 7200, UTC)


async def test_mass_outage_escalates_after_max_hold(engine):
    from agentmon.engine.app import OUTAGE_NOTE
    now = await seed(engine)
    await engine.evaluate_cycle()
    _cortex_down(engine, now)
    await engine.evaluate_cycle()
    out = engine.outages["cortex"]
    assert all(cortex_state(engine, i) == OK for i in range(N))          # ushlab turilmoqda

    out.started -= engine.s.mass_outage_max_hold + 60                      # 4 soatdan ko'p o'tdi
    for _ in range(2):                                                     # debounce
        await engine.evaluate_cycle()
    assert all(cortex_state(engine, i) == "STOPPED" for i in range(N))
    t = engine.tracked[(engine.hosts["pc-00"].id, "cortex")]
    assert OUTAGE_NOTE in t.reason
    inc = await engine.pool.fetchrow("SELECT ended_at, details FROM incident WHERE kind = 'mass_outage'")
    assert inc["ended_at"] is None and inc["details"]["escalated"] is True   # holat o'zgargani insidentni yopmaydi

    for i in range(N):                                                     # tiklandi
        engine.store.last[(f"10.10.0.{i + 1}", "cortex")] = int(time.time())
    await engine.evaluate_cycle()
    assert "cortex" not in engine.outages and cortex_state(engine, 0) == OK
    assert await engine.pool.fetchval("SELECT ended_at FROM incident WHERE kind = 'mass_outage'") is not None


async def test_mass_outage_survives_night_and_restart(dsn, engine):
    now = await seed(engine)
    await engine.evaluate_cycle()
    _cortex_down(engine, now)
    await engine.evaluate_cycle()
    assert "cortex" in engine.outages

    for i in range(N):                                                     # tun: hamma kompyuter o'chdi
        engine.store.presence[f"10.10.0.{i + 1}"].last_seen = now - 3 * 3600
    await engine.evaluate_cycle()
    assert "cortex" in engine.outages                                      # ma'lumot yo'q — insident yopilmaydi

    await engine.flush()
    await engine.close()
    again = Engine(settings(dsn))
    await again.start()
    try:
        out = again.outages["cortex"]
        assert len(out.base) == N and not out.escalated                     # bazaviy ro'yxat DB'dan tiklandi
    finally:
        await again.close()
    engine.close = _noop  # type: ignore[method-assign]


def _exporter_traffic(eng: Engine, exp: str, ips: list[str], last: int) -> None:
    from agentmon.engine.signals import ExporterStat
    for ip in ips:
        eng.store.presence[ip].exporter = exp
        eng.store.presence[ip].last_seen = last
    eng.store.stats.exporters[exp] = ExporterStat(last, 1000)


async def test_abrupt_exporter_silence_opens_incident(engine):
    now = await seed(engine)
    _exporter_traffic(engine, "172.25.0.1", [f"10.10.0.{i + 1}" for i in range(10)], now - 5)          # markaz: ishlaydi
    _exporter_traffic(engine, "172.25.0.2", [f"10.10.0.{i + 1}" for i in range(10, 20)], now - 1200)   # filial: 20 daq jim
    await engine.evaluate_cycle()
    states = engine._exporter_states()
    assert states == {"172.25.0.1": "ok", "172.25.0.2": "stale"}
    inc = await engine.pool.fetchrow("SELECT details, ended_at FROM incident WHERE kind = 'exporter_stale'")
    assert inc["details"]["exporter"] == "172.25.0.2" and inc["ended_at"] is None

    _exporter_traffic(engine, "172.25.0.2", [f"10.10.0.{i + 1}" for i in range(10, 20)], now)          # tiklandi
    await engine.evaluate_cycle()
    assert await engine.pool.fetchval("SELECT ended_at FROM incident WHERE kind = 'exporter_stale'") is not None


async def test_branch_closing_for_night_is_not_an_incident(engine):
    now = await seed(engine)
    _exporter_traffic(engine, "172.25.0.1", [f"10.10.0.{i + 1}" for i in range(18)], now - 5)
    # Filialda kompyuterlar birin-ketin o'chgan: jimlikdan oldingi 2 daqiqada faqat 2 ta host bor edi.
    _exporter_traffic(engine, "172.25.0.2", ["10.10.0.19", "10.10.0.20"], now - 1200)
    await engine.evaluate_cycle()
    assert engine._exporter_states()["172.25.0.2"] == "quiet"
    assert await engine.pool.fetchval("SELECT count(*) FROM incident WHERE kind = 'exporter_stale'") == 0


async def test_configured_exporter_that_never_sent_is_reported(dsn, engine):
    await engine.close()
    eng = Engine(settings(dsn, nsel_exporters="172.25.0.1,172.25.0.9"))
    await eng.start()
    try:
        now = await seed(eng)
        _exporter_traffic(eng, "172.25.0.1", [f"10.10.0.{i + 1}" for i in range(N)], now - 5)
        eng.started -= 3600
        await eng.evaluate_cycle()
        assert eng._exporter_states() == {"172.25.0.1": "ok", "172.25.0.9": "missing"}
        await eng.flush()                                                   # holat DB'ga (system_status) yoziladi
    finally:
        await eng.close()
    again = Engine(settings(dsn, nsel_exporters="172.25.0.1,172.25.0.9"))
    await again.start()
    try:
        assert again.store.stats.exporters["172.25.0.1"].received == 1000   # restartdan keyin tiklandi
        assert "172.25.0.9" in again.exporter_incidents
    finally:
        await again.close()
    engine.close = _noop  # type: ignore[method-assign]


async def test_failed_write_does_not_lose_state(engine, monkeypatch):
    await seed(engine, missing_cortex={2})
    real = engine._write_changes

    async def boom(*a, **kw):
        raise RuntimeError("DB yo'q")

    monkeypatch.setattr(engine, "_write_changes", boom)
    with pytest.raises(RuntimeError):
        await engine.evaluate_cycle()
    assert not engine.tracked                                  # xotira DB bilan mos qoldi
    monkeypatch.setattr(engine, "_write_changes", real)
    await engine.evaluate_cycle()
    assert cortex_state(engine, 2) == NOT_INSTALLED
    assert await engine.pool.fetchval("SELECT count(*) FROM host_state") == N * 4


async def test_exclusion_applies_without_inventory_sync(engine):
    now = await seed(engine)
    await engine.evaluate_cycle()
    await engine.pool.execute("UPDATE host SET excluded = true WHERE name = 'pc-07'")
    engine.store.last[("10.10.0.8", "cortex")] = now - 7200
    await engine.evaluate_cycle()
    await engine.evaluate_cycle()
    assert cortex_state(engine, 7) == OK                       # istisno — baholanmadi


async def test_suspicious_drop_rejected_then_accepted(engine):
    big = [ConsoleRecord("cortex", f"h{i}", f"h{i}", True) for i in range(200)]
    await engine._replace_console("cortex", big)
    small = big[:50]
    for _ in range(2):
        with pytest.raises(RuntimeError, match="keskin kamayish"):
            await engine._replace_console("cortex", small)
    assert await engine._replace_console("cortex", small) == 50   # 3-marta takrorlandi — haqiqiy


async def test_restart_restores_state_and_single_instance(dsn, engine):
    await seed(engine, missing_cortex={1})
    await engine.flush()
    await engine.evaluate_cycle()

    second = Engine(settings(dsn))
    start = asyncio.create_task(second.start())
    await asyncio.sleep(1.0)
    assert not start.done()                                    # birinchisi ishlayotganda kutadi
    start.cancel()
    try:
        await start
    except asyncio.CancelledError:
        pass
    if second.pool is not None:
        second.pool.terminate()

    await engine.close()
    third = Engine(settings(dsn))
    await asyncio.wait_for(third.start(), 15)
    try:
        h = third.hosts["pc-01"]
        assert third.tracked[(h.id, "cortex")].state == NOT_INSTALLED
        assert "10.10.0.2" in third.store.presence
    finally:
        await third.close()
    # fixture close() ikkinchi marta chaqirilmasligi uchun
    engine.close = _noop  # type: ignore[method-assign]


async def _noop():
    return None


async def test_disabled_product_is_not_evaluated(dsn, engine):
    """AD o'chirilganda (DC trafigi ham yo'q) — hech kim "DC bilan aloqa yo'q" bo'lib qolmasligi kerak."""
    await engine.close()
    eng = Engine(settings(dsn, products_enabled="cortex,ksc,si", dc_ips=""))
    await eng.start()
    try:
        now = await seed(eng)
        for i in range(N):
            eng.store.last.pop((f"10.10.0.{i + 1}", "ad"), None)   # DC trafigi umuman yo'q
        await eng.evaluate_cycle()
        products = {p for (_hid, p) in eng.tracked}
        assert "ad" not in products and {"cortex", "ksc", "si"} <= products
        assert await eng.pool.fetchval("SELECT count(*) FROM host_state WHERE product = 'ad'") == 0
        assert now
    finally:
        await eng.close()
    engine.close = _noop  # type: ignore[method-assign]


async def test_stale_identity_is_flagged_in_reason(engine):
    """AD'siz rejim: IP hostga faqat eski agent dalili bilan bog'langan bo'lsa, jimlik xulosasi ogohlantirish oladi."""
    from datetime import timedelta
    now = await seed(engine)
    engine.dns = []                                            # DNS yo'q (AD'siz rejim)
    stale = engine.consoles["cortex"]["pc-03"]
    stale.last_seen = datetime.fromtimestamp(now, UTC) - timedelta(days=3)
    engine.store.last.pop(("10.10.0.4", "cortex"), None)      # pc-03 IP'sidan Cortex trafigi yo'q
    await engine.evaluate_cycle()

    t_stale = engine.tracked[(engine.hosts["pc-03"].id, "cortex")]
    assert t_stale.state == "STOPPED" and "eski dalil" in t_stale.reason
    t_fresh = engine.tracked[(engine.hosts["pc-04"].id, "cortex")]
    assert t_fresh.state == "OK"
    # Yangi dalil bilan bog'langan hostning jimlik xulosasida ogohlantirish bo'lmasligi kerak:
    t_si = engine.tracked[(engine.hosts["pc-04"].id, "si")]
    assert t_si.state == "NO_SIGNAL" and "eski dalil" not in t_si.reason


async def test_dhcp_reuse_does_not_blame_previous_owner(engine):
    """pc-05 kecha ketgan; bugun uning IP'sini domensiz, agentsiz qurilma ishlatmoqda.
    pc-05 "to'xtatilgan" deb ayblanmasligi, IP esa noma'lum qurilma bo'lib ko'rinishi kerak."""
    now = await seed(engine)
    yesterday = now - 16 * 3600
    engine.dns = [c if c.name != "pc-05" else Candidate(c.ip, c.name, "dns", yesterday) for c in engine.dns]
    engine.consoles["cortex"]["pc-05"].last_seen = datetime.fromtimestamp(yesterday, UTC)
    ip = "10.10.0.6"
    engine.store.presence[ip] = Presence("Markaz", now - 86400, now - 3600, now - 10)   # yangi seans: 1 soat
    engine.store.last[(ip, "ad")] = engine.store.last[(ip, "ad-auth")] = yesterday + 60   # DC trafigi — faqat kecha
    engine.store.last.pop((ip, "cortex"), None)
    await engine.evaluate_cycle()

    assert engine.tracked[(engine.hosts["pc-05"].id, "cortex")].state == "OFFLINE"
    assert await engine.pool.fetchval("SELECT count(*) FROM host_ip WHERE ip = $1::inet", ip) == 0
    assert cortex_state(engine, 4) == OK                         # qo'shnilarga ta'sir yo'q


async def test_demo_live_refuses_without_demo_data(dsn, engine, monkeypatch):
    from agentmon import demo_live
    from agentmon.config import get_settings
    monkeypatch.setenv("DEMO_LIVE", "yes")
    for k in ("AD_SERVER", "CORTEX_FQDN", "KSC_URL"):
        monkeypatch.setenv(k, "")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="demo"):
        await demo_live.guard(dsn)                                 # bo'sh baza — 1-bosqich (faqat FTD) holati
    await engine.pool.execute("INSERT INTO host (name, display_name, sources) VALUES ('buxgalter-01', 'X', '{ksc}')")
    with pytest.raises(SystemExit):
        await demo_live.guard(dsn)                                 # real kompyuter bor
    await engine.pool.execute("DELETE FROM host")
    await engine.pool.execute("INSERT INTO host (name, display_name, sources) VALUES ('tash-pc-0001', 'X', '{ad}')")
    await demo_live.guard(dsn)                                     # faqat demo — ruxsat
    get_settings.cache_clear()


async def test_phase1_ftd_only_without_any_console(engine):
    """1-bosqich: faqat NetFlow, hech qaysi konsol yo'q — engine xatosiz ishlaydi, IP'lar noma'lum qurilma bo'ladi."""
    now = int(time.time())
    for i in range(5):
        engine.store.observe(f"10.10.1.{i + 1}", "Markaz", "cortex" if i % 2 else None, now - 30)
    engine.store.stats.last_rx_wall = time.time()
    engine.store.stats.watermark = now
    engine.data_since = time.time() - 3600
    await engine.flush()
    await engine.evaluate_cycle()                                 # xato bermasligi kerak
    assert not engine.hosts and not engine.tracked
    unknown = await engine.pool.fetchval(
        """SELECT count(*) FROM ip_presence p WHERE NOT EXISTS (SELECT 1 FROM host_ip h WHERE h.ip = p.ip)""")
    assert unknown == 5
    status = await engine.pool.fetchval("SELECT value->>'hosts' FROM system_status WHERE key = 'engine'")
    assert status == "0"


async def test_lost_lock_stops_engine(engine, monkeypatch, tmp_path):
    from agentmon.engine import app as app_mod
    monkeypatch.setattr(app_mod, "HEARTBEAT_FILE", tmp_path / "alive")
    assert await engine._lock_held()
    await engine._lock_conn.execute("SELECT pg_advisory_unlock($1)", app_mod.ENGINE_LOCK_ID)   # ulanish uzilgandek
    await asyncio.wait_for(engine._heartbeat_loop(), 5)
    assert engine.stopping.is_set() and engine.exit_code == 1
    assert (tmp_path / "alive").exists()
