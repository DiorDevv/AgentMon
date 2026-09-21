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
    eng = Engine(settings(dsn))
    await eng.start()
    await eng.pool.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    eng.tracked.clear()
    eng.outages.clear()
    eng.collector_incident = None
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
