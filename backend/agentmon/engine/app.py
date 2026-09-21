"""Engine: tarmoq signallari + inventar + qoidalar -> host holatlari.

Bitta jarayon, bir nechta asyncio vazifasi:
  ingest     Redis'dan NSEL hodisalarini o'qib, xotiradagi signallarni yangilaydi
  flush      o'zgargan signallarni PostgreSQL'ga yozadi
  inventory  AD / Cortex / KSC'dan inventarni sinxronlaydi
  evaluate   IP->host moslash, qoidalar, holat o'zgarishlari, statistika
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import signal
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from agentmon import db
from agentmon.config import PRODUCTS, Settings, get_settings
from agentmon.engine.classify import Classifier
from agentmon.engine.hosts import build_hosts
from agentmon.engine.identity import Candidate, resolve, verify
from agentmon.engine.inventory import ad as ad_inv
from agentmon.engine.inventory import cortex as cortex_inv
from agentmon.engine.inventory import ksc as ksc_inv
from agentmon.engine.rules import (
    C_MISSING,
    C_NONE,
    C_PRESENT,
    C_UNKNOWN,
    ConsoleEvidence,
    Evidence,
    Tracked,
    apply_debounce,
    effective_state,
    evaluate,
)
from agentmon.engine.signals import EV_UPDATE, Presence, SignalStore, denied_group
from agentmon.model import OK, ConsoleRecord, dedupe_latest

log = logging.getLogger("agentmon.engine")
UTC = timezone.utc
CONSOLE_PRODUCTS = ("ad", "cortex", "ksc")
REDIS_BATCH = 2000
RECENT_HOST_SECONDS = 7 * 86400   # hisobotlarga kiradigan hostlar: so'nggi 7 kunda tirik bo'lganlar
ENGINE_LOCK_ID = 7_310_001        # faqat bitta engine nusxasi ishlashi uchun (pg advisory lock)
DNS_TRUST_AGE = 2 * 86400         # bundan eski DNS dalili DC trafigi bilan tasdiqlanishi kerak
DC_TRAFFIC_WINDOW = 7 * 86400
SIGNAL_RETENTION = 30 * 86400
SUSPICIOUS_REPEATS = 3            # keskin kamaygan inventar shuncha marta takrorlansa — haqiqiy deb qabul qilinadi


def dt(ts: int | float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, UTC) if ts is not None else None


def ts_of(d: datetime | None) -> int | None:
    return int(d.timestamp()) if d else None


@dataclass(slots=True)
class HostRow:
    id: int
    name: str
    excluded: bool
    active: bool   # kamida bitta manbada bor


class Engine:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.pool = None
        self.redis: aioredis.Redis | None = None
        self.store = SignalStore(s.alive_window)
        self.clf = Classifier.from_settings(s)
        self.consoles: dict[str, dict[str, ConsoleRecord]] = {p: {} for p in CONSOLE_PRODUCTS}
        self.source_ok: dict[str, int] = {}
        self.dns: list[Candidate] = []
        self.hosts: dict[str, HostRow] = {}
        self.host_by_id: dict[int, HostRow] = {}
        self.ip_map: dict[str, tuple[int, str, int | None, int]] = {}
        self.host_ips: dict[int, list[str]] = {}
        self.tracked: dict[tuple[int, str], Tracked] = {}
        self.outages: dict[str, int] = {}
        self.collector_incident: int | None = None
        self.last_snapshot_hour: int | None = None
        self.started = time.time()
        self.data_since: float | None = None   # uzluksiz NSEL oqimi boshlangan vaqt (isinish davri uchun)
        self.stopping = asyncio.Event()
        self.state_lock = asyncio.Lock()        # hostlar ro'yxatini qayta qurish va baholash bir-biriga xalaqit bermasin
        self._suspicious: dict[str, tuple[int, int]] = {}
        self._lock_conn = None
        self._eps_prev: tuple[float, int] = (time.time(), 0)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.pool = await db.create_pool(self.s.database_url, min_size=2, max_size=6)
        await db.migrate(self.pool)
        await self._acquire_single_instance()
        self.redis = aioredis.from_url(self.s.redis_url)
        await self._load()
        log.info("engine ishga tushdi: %d host, %d IP signal", len(self.hosts), len(self.store.presence))

    async def _acquire_single_instance(self) -> None:
        """Ikki engine bir vaqtda ishlasa, Redis navbati bo'linib ketadi va holatlar buziladi."""
        self._lock_conn = await self.pool.acquire()
        while not await self._lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", ENGINE_LOCK_ID):
            log.warning("Boshqa engine nusxasi ishlamoqda — kutilmoqda")
            await asyncio.sleep(10)

    async def run(self) -> None:
        await self.start()
        tasks = [asyncio.create_task(c, name=n) for n, c in (
            ("ingest", self._ingest_loop()),
            ("flush", self._periodic(self.s.flush_interval, self.flush)),
            ("inventory", self._periodic(self.s.inventory_interval, self.sync_inventory)),
            ("evaluate", self._periodic(self.s.eval_interval, self.evaluate_cycle, delay=15)),
            ("maintenance", self._periodic(6 * 3600, self.maintenance, delay=300)),
        )]
        await self.stopping.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.flush()
        await self.close()
        log.info("engine to'xtadi")

    async def close(self) -> None:
        await self.redis.aclose()
        if self._lock_conn is not None:
            await self.pool.release(self._lock_conn)
            self._lock_conn = None
        await self.pool.close()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stopping.wait(), seconds)
        except TimeoutError:
            pass

    async def _periodic(self, interval: int, fn, delay: float = 0) -> None:
        await self._sleep(delay)
        while not self.stopping.is_set():
            t0 = time.monotonic()
            try:
                await fn()
            except Exception:
                log.exception("%s xatolik bilan tugadi", fn.__name__)
            await self._sleep(max(1.0, interval - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ load
    async def _load(self) -> None:
        p = self.pool
        presence = {
            r["ip"]: Presence(r["site"] or "", ts_of(r["first_seen"]), ts_of(r["alive_since"]), ts_of(r["last_seen"]))
            for r in await p.fetch("SELECT host(ip) AS ip, site, first_seen, alive_since, last_seen FROM ip_presence")
        }
        last = {(r["ip"], r["grp"]): ts_of(r["last_seen"])
                for r in await p.fetch("SELECT host(ip) AS ip, grp, last_seen FROM net_signal")}
        self.store.load(presence, last)

        for r in await p.fetch(
            """SELECT product, name, display_name, fqdn, os, array(SELECT host(x) FROM unnest(ips) x) AS ips,
                      last_seen, healthy, reason, version, details FROM console_endpoint"""):
            self.consoles.setdefault(r["product"], {})[r["name"]] = ConsoleRecord(
                product=r["product"], name=r["name"], display_name=r["display_name"] or r["name"],
                healthy=r["healthy"], reason=r["reason"], fqdn=r["fqdn"], os=r["os"], ips=list(r["ips"]),
                last_seen=r["last_seen"], version=r["version"], details=r["details"] or {})
        for r in await p.fetch("SELECT source, last_success FROM source_status WHERE last_success IS NOT NULL"):
            self.source_ok[r["source"]] = ts_of(r["last_success"])
        self.dns = [Candidate(r["ip"], r["name"], "dns", ts_of(r["observed_at"]))
                    for r in await p.fetch("SELECT host(ip) AS ip, name, observed_at FROM dns_record")]
        await self._rebuild_classifier()
        await self._load_hosts()

        for r in await p.fetch("SELECT * FROM host_state"):
            self.tracked[(r["host_id"], r["product"])] = Tracked(
                r["state"], r["reason"], ts_of(r["since"]), r["last_known"], r["last_known_reason"])
        for r in await p.fetch("SELECT id, kind, product FROM incident WHERE ended_at IS NULL"):
            if r["kind"] == "mass_outage":
                self.outages[r["product"]] = r["id"]
            elif r["kind"] == "collector_stale":
                self.collector_incident = r["id"]

    async def _load_hosts(self) -> None:
        rows = await self.pool.fetch("SELECT id, name, excluded, cardinality(sources) > 0 AS active FROM host")
        self.hosts = {r["name"]: HostRow(r["id"], r["name"], r["excluded"], r["active"]) for r in rows}
        self.host_by_id = {h.id: h for h in self.hosts.values()}
        self.ip_map = {
            r["ip"]: (r["host_id"], r["source"], ts_of(r["observed_at"]), r["alternatives"])
            for r in await self.pool.fetch("SELECT host(ip) AS ip, host_id, source, observed_at, alternatives FROM host_ip")
        }
        self._index_host_ips()

    def _index_host_ips(self) -> None:
        idx: dict[int, list[str]] = {}
        for ip, (hid, *_rest) in self.ip_map.items():
            idx.setdefault(hid, []).append(ip)
        self.host_ips = idx

    async def _rebuild_classifier(self) -> None:
        subnets = [(r["cidr"], r["site"]) for r in
                   await self.pool.fetch("SELECT cidr::text AS cidr, site FROM net_subnet WHERE source = 'ad'")]
        dcs = {r["ip"] for r in await self.pool.fetch("SELECT host(ip) AS ip FROM dc_server")}
        self.clf = Classifier.from_settings(self.s, ad_dc_ips=dcs, ad_subnets=subnets)

    # ------------------------------------------------------------------ ingest
    async def _ingest_loop(self) -> None:
        key = self.s.redis_key
        while not self.stopping.is_set():
            try:
                batch = await self.redis.lpop(key, REDIS_BATCH)
            except RedisError as e:
                log.warning("Redis xatosi: %s", e)
                await self._sleep(2)
                continue
            if not batch:
                await self._sleep(0.25)
                continue
            now = int(time.time())
            clf, store = self.clf, self.store
            try:
                for raw in batch:
                    store.process_raw(raw, clf, now)
            except Exception:  # noqa: BLE001 — bitta buzuq hodisa ingest'ni to'xtatib qo'ymasin
                log.exception("Hodisani qayta ishlashda kutilmagan xato")
            if self._collector_stale():
                self.data_since = None           # uzilishdan keyin isinish davri qaytadan boshlanadi
            if self.data_since is None:
                self.data_since = time.time()
            store.stats.last_rx_wall = time.time()
            await asyncio.sleep(0)   # boshqa vazifalarga navbat

    async def flush(self) -> None:
        pres, sig = self.store.take_dirty()
        try:
            async with self.pool.acquire() as c, c.transaction():
                if pres:
                    await c.execute(
                        """INSERT INTO ip_presence (ip, site, first_seen, alive_since, last_seen)
                           SELECT v.ip::inet, v.site, v.fs, v.al, v.ls
                           FROM unnest($1::text[], $2::text[], $3::timestamptz[], $4::timestamptz[], $5::timestamptz[])
                                AS v(ip, site, fs, al, ls)
                           ON CONFLICT (ip) DO UPDATE SET site = EXCLUDED.site,
                               alive_since = EXCLUDED.alive_since, last_seen = EXCLUDED.last_seen""",
                        [ip for ip, _ in pres], [p.site for _, p in pres], [dt(p.first_seen) for _, p in pres],
                        [dt(p.alive_since) for _, p in pres], [dt(p.last_seen) for _, p in pres])
                if sig:
                    await c.execute(
                        """INSERT INTO net_signal (ip, grp, last_seen)
                           SELECT v.ip::inet, v.grp, v.ls FROM unnest($1::text[], $2::text[], $3::timestamptz[]) AS v(ip, grp, ls)
                           ON CONFLICT (ip, grp) DO UPDATE SET last_seen = GREATEST(net_signal.last_seen, EXCLUDED.last_seen)""",
                        [s[0] for s in sig], [s[1] for s in sig], [dt(s[2]) for s in sig])
        except Exception:
            self.store.restore_dirty(pres, sig)
            raise
        await self._write_collector_status()

    async def _write_collector_status(self) -> None:
        st = self.store.stats
        now = time.time()
        t_prev, n_prev = self._eps_prev
        eps = (st.received - n_prev) / max(now - t_prev, 1e-6)
        self._eps_prev = (now, st.received)
        try:
            queue = await self.redis.llen(self.s.redis_key)
        except RedisError:
            queue = None
        await db.set_status(self.pool, "collector", {
            "received": st.received, "accepted": st.accepted, "malformed": st.malformed,
            "by_event": {str(k): v for k, v in st.by_event.items()},
            "eps": round(eps, 1), "queue": queue,
            "last_rx": dt(st.last_rx_wall).isoformat() if st.last_rx_wall else None,
            "watermark": dt(st.watermark).isoformat() if st.watermark else None,
            "stale": self._collector_stale(),
            # flow-update hodisalari kelmasa — FTD'da refresh-interval sozlanmagan.
            "update_events_seen": bool(st.last_update_event and now - st.last_update_event < 3600),
            "ev_update_code": EV_UPDATE,
            "tracked_ips": len(self.store.presence),
        })

    def _collector_stale(self) -> bool:
        st = self.store.stats
        if st.last_rx_wall is None:
            return time.time() - self.started > self.s.collector_stale
        return time.time() - st.last_rx_wall > self.s.collector_stale

    # ------------------------------------------------------------------ inventory
    async def sync_inventory(self) -> None:
        s = self.s
        # source_status DB'dagi haqiqat: boshqa jarayon (masalan, qayta ishga tushirishdan oldingi engine) yozgan bo'lishi mumkin.
        for r in await self.pool.fetch("SELECT source, last_success FROM source_status WHERE last_success IS NOT NULL"):
            ts = ts_of(r["last_success"])
            if ts > self.source_ok.get(r["source"], 0):
                self.source_ok[r["source"]] = ts
        jobs = []
        if s.ad_server and s.ad_base_dn:
            jobs.append(self._run_source("ad", self._sync_ad))
        if s.cortex_fqdn and s.cortex_key:
            jobs.append(self._run_source("cortex", lambda: self._sync_console("cortex", cortex_inv.fetch(s))))
        if s.ksc_url and s.ksc_user:
            jobs.append(self._run_source("ksc", lambda: self._sync_console("ksc", ksc_inv.fetch(s))))
        if not jobs:
            log.warning("Hech qaysi inventar manbasi sozlanmagan")
            return
        await asyncio.gather(*jobs)
        await self._rebuild_hosts()

    async def _run_source(self, name: str, fn) -> None:
        await self.pool.execute(
            """INSERT INTO source_status (source, last_attempt) VALUES ($1, now())
               ON CONFLICT (source) DO UPDATE SET last_attempt = now()""", name)
        t0 = time.monotonic()
        try:
            count = await fn()
        except Exception as e:  # noqa: BLE001 — bitta manba xatosi boshqalarini to'xtatmasin
            log.exception("%s sinxronlash xatosi", name)
            await self.pool.execute("UPDATE source_status SET last_error = $2 WHERE source = $1",
                                    name, f"{type(e).__name__}: {e}"[:1000])
            return
        self.source_ok[name] = int(time.time())
        await self.pool.execute(
            "UPDATE source_status SET last_success = now(), last_error = NULL, item_count = $2 WHERE source = $1",
            name, count)
        log.info("%s: %d ta yozuv, %.1fs", name, count, time.monotonic() - t0)

    async def _sync_console(self, product: str, fetch_coro) -> int:
        return await self._replace_console(product, await fetch_coro)

    async def _replace_console(self, product: str, records: list[ConsoleRecord]) -> int:
        records = dedupe_latest(records)
        prev = len(self.consoles.get(product, {}))
        # Himoya: API noto'g'ri/yarim javob qaytarsa, butun ro'yxat "o'rnatilmagan" bo'lib ketmasin.
        # Lekin bir xil natija ketma-ket takrorlansa — bu haqiqiy o'zgarish (masalan, eski yozuvlar tozalangan).
        if prev >= 100 and len(records) < prev * 0.5:
            last_n, repeats = self._suspicious.get(product, (-1, 0))
            repeats = repeats + 1 if abs(len(records) - last_n) <= max(5, last_n * 0.02) else 1
            self._suspicious[product] = (len(records), repeats)
            if repeats < SUSPICIOUS_REPEATS:
                raise RuntimeError(f"{len(records)} ta yozuv keldi (oldin {prev}) — keskin kamayish, "
                                   f"qabul qilinmadi ({repeats}/{SUSPICIOUS_REPEATS})")
            log.warning("%s: keskin kamayish %d marta tasdiqlandi — qabul qilinmoqda", product, repeats)
        self._suspicious.pop(product, None)
        now = datetime.now(UTC)
        async with self.pool.acquire() as c, c.transaction():
            await c.execute("DELETE FROM console_endpoint WHERE product = $1", product)
            await c.executemany(
                """INSERT INTO console_endpoint (product, name, display_name, fqdn, os, ips, last_seen,
                                                 healthy, reason, version, details, synced_at)
                   VALUES ($1, $2, $3, $4, $5, $6::text[]::inet[], $7, $8, $9, $10, $11, $12)""",
                [(product, r.name, r.display_name, r.fqdn, r.os, r.ips, r.last_seen, r.healthy, r.reason,
                  r.version, r.details, now) for r in records])
        self.consoles[product] = {r.name: r for r in records}
        return len(records)

    async def _sync_ad(self) -> int:
        inv = await asyncio.to_thread(ad_inv.ADClient(self.s).fetch, datetime.now(UTC))
        count = await self._replace_console("ad", inv.computers)
        subnets = []
        for cidr, site in inv.subnets:
            try:
                subnets.append((str(ipaddress.ip_network(cidr, strict=False)), site or cidr))
            except ValueError:
                log.warning("AD subnet noto'g'ri: %r", cidr)
        async with self.pool.acquire() as c, c.transaction():
            await c.execute("DELETE FROM dns_record")
            await c.executemany(
                "INSERT INTO dns_record (ip, name, fqdn, observed_at) VALUES ($1::inet, $2, $3, $4) ON CONFLICT DO NOTHING",
                [(d.ip, d.name, d.fqdn, d.observed_at) for d in inv.dns])
            await c.execute("DELETE FROM net_subnet WHERE source = 'ad'")
            await c.executemany(
                "INSERT INTO net_subnet (cidr, site, source) VALUES ($1::cidr, $2, 'ad') ON CONFLICT DO NOTHING", subnets)
            await c.execute("DELETE FROM dc_server")
            await c.executemany("INSERT INTO dc_server (ip, name) VALUES ($1::inet, $2) ON CONFLICT DO NOTHING", inv.dcs)
        self.dns = [Candidate(d.ip, d.name, "dns", ts_of(d.observed_at)) for d in inv.dns]
        await self._rebuild_classifier()
        log.info("AD: %d DNS yozuv, %d subnet, %d DC IP", len(inv.dns), len(subnets), len(inv.dcs))
        return count

    async def _rebuild_hosts(self) -> None:
        async with self.state_lock:
            await self._rebuild_hosts_locked()

    async def _rebuild_hosts_locked(self) -> None:
        infos = build_hosts(self.consoles, self.s.include_servers)
        async with self.pool.acquire() as c, c.transaction():
            await c.executemany(
                """INSERT INTO host (name, display_name, fqdn, os, sources) VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (name) DO UPDATE SET display_name = EXCLUDED.display_name, fqdn = EXCLUDED.fqdn,
                       os = EXCLUDED.os, sources = EXCLUDED.sources, updated_at = now()""",
                [(h.name, h.display_name, h.fqdn, h.os, h.sources) for h in infos.values()])
            await c.execute(
                """UPDATE host SET sources = '{}', updated_at = now()
                   WHERE cardinality(sources) > 0 AND NOT (name = ANY($1::text[]))""", list(infos))
            await c.execute(
                """DELETE FROM host WHERE cardinality(sources) = 0
                   AND coalesce(last_alive, first_seen) < now() - interval '90 days'""")
        await self._load_hosts()
        live_ids = set(self.host_by_id)
        self.tracked = {k: v for k, v in self.tracked.items() if k[0] in live_ids}

    # ------------------------------------------------------------------ identity
    async def _resolve_identity(self) -> None:
        cands = list(self.dns)
        for product in ("cortex", "ksc"):
            for r in self.consoles.get(product, {}).values():
                seen = ts_of(r.last_seen)
                if seen is None:
                    continue
                cands.extend(Candidate(ip, r.name, product, seen) for ip in r.ips)
        now = int(time.time())
        resolved = resolve(cands, now, self.s.identity_max_age)
        store = self.store

        def trustworthy(ip: str) -> bool:
            # Hozir faol bo'lmagan IP xavf tug'dirmaydi; faol bo'lsa — domen trafigi bo'lishi kerak.
            pres = store.presence.get(ip)
            if pres is None or now - pres.last_seen > self.s.alive_window:
                return True
            dc = store.last.get((ip, "ad"))
            return dc is not None and now - dc <= DC_TRAFFIC_WINDOW

        resolved = verify(resolved, now, DNS_TRUST_AGE, trustworthy)

        new_map: dict[str, tuple[int, str, int | None, int]] = {}
        for ip, res in resolved.items():
            h = self.hosts.get(res.name)
            if h and h.active:
                new_map[ip] = (h.id, res.source, res.observed_at, res.alternatives)

        removed = [ip for ip in self.ip_map if ip not in new_map]
        changed = [(ip, v) for ip, v in new_map.items() if self.ip_map.get(ip) != v]
        if removed or changed:
            async with self.pool.acquire() as c, c.transaction():
                if removed:
                    await c.execute("DELETE FROM host_ip WHERE ip = ANY($1::text[]::inet[])", removed)
                if changed:
                    await c.executemany(
                        """INSERT INTO host_ip (ip, host_id, source, observed_at, alternatives, updated_at)
                           VALUES ($1::inet, $2, $3, $4, $5, now())
                           ON CONFLICT (ip) DO UPDATE SET host_id = EXCLUDED.host_id, source = EXCLUDED.source,
                               observed_at = EXCLUDED.observed_at, alternatives = EXCLUDED.alternatives, updated_at = now()""",
                        [(ip, v[0], v[1], dt(v[2]), v[3]) for ip, v in changed])
        self.ip_map = new_map
        self._index_host_ips()

    # ------------------------------------------------------------------ evaluate
    def _console_evidence(self, product: str, name: str, now: int) -> ConsoleEvidence:
        if product == "si":
            return ConsoleEvidence(C_NONE)
        ok_at = self.source_ok.get(product)
        if ok_at is None or now - ok_at > self.s.source_max_age:
            return ConsoleEvidence(C_UNKNOWN)
        r = self.consoles.get(product, {}).get(name)
        if r is None:
            return ConsoleEvidence(C_MISSING)
        return ConsoleEvidence(C_PRESENT, r.healthy, r.reason, ts_of(r.last_seen), track_fresh=product != "ad")

    async def evaluate_cycle(self) -> None:
        async with self.state_lock:
            await self._evaluate_locked()

    async def _evaluate_locked(self) -> None:
        t0 = time.monotonic()
        now_wall = int(time.time())
        await self._update_collector_incident()
        if self._collector_stale() or self.store.stats.watermark is None:
            log.warning("Kollektor ma'lumoti eskirgan — holatlar muzlatildi")
            return
        # Isinish: engine ishga tushgandan yoki NSEL uzilishidan keyin barcha hostlar bir oyna davomida
        # o'z trafigini yuborib ulgurishi kerak — aks holda hammasi bir zumda "oflayn" bo'lib ko'rinadi.
        if self.data_since is None or time.time() - self.data_since < self.s.alive_window:
            log.info("Isinish davri: NSEL ma'lumoti yig'ilmoqda, baholash keyinroq")
            return
        for r in await self.pool.fetch("SELECT id, excluded FROM host"):
            h = self.host_by_id.get(r["id"])
            if h is not None:
                h.excluded = r["excluded"]
        # Hisob-kitob hodisalar vaqti bo'yicha: engine orqada qolsa ham hostlar "jim" bo'lib ketmaydi.
        ref = min(now_wall, self.store.stats.watermark)

        await self._resolve_identity()
        s, store, thresholds = self.s, self.store, self.s.thresholds

        cands: dict[tuple[int, str], tuple[str, str | None, bool, int | None]] = {}
        alive_rows: list[tuple[int, datetime, str]] = []
        recent: set[int] = set()
        judge_after = {p: max(s.grace, thresholds[p]) for p in PRODUCTS}
        judgeable: dict[str, list[tuple[int, str]]] = {p: [] for p in PRODUCTS}

        for h in self.hosts.values():
            if not h.active or h.excluded:
                continue
            ips = self.host_ips.get(h.id, [])
            best: Presence | None = None
            for ip in ips:
                p = store.presence.get(ip)
                if p and (best is None or p.last_seen > best.last_seen):
                    best = p
            alive = best is not None and ref - best.last_seen <= s.alive_window
            alive_for = ref - best.alive_since if alive else 0
            if best:
                if ref - best.last_seen <= RECENT_HOST_SECONDS:
                    recent.add(h.id)
                if alive:
                    alive_rows.append((h.id, dt(best.last_seen), best.site))

            for product in PRODUCTS:
                net_last = max((store.last.get((ip, product)) or 0 for ip in ips), default=0) or None
                denied = max((store.last.get((ip, denied_group(product))) or 0 for ip in ips), default=0) or None
                console = self._console_evidence(product, h.name, now_wall)
                state, reason = evaluate(Evidence(ref, alive, alive_for, net_last, thresholds[product], s.grace,
                                                  console, denied))
                if not ips:
                    reason = "IP manzili aniqlanmadi yoki tasdiqlanmadi (DNS'da ham, agentlarda ham ishonchli dalil yo'q)"
                silent = alive and (net_last is None or ref - net_last > thresholds[product])
                cands[(h.id, product)] = (state, reason, silent, net_last)
                if alive and alive_for >= judge_after[product]:
                    judgeable[product].append((h.id, product))

        held = await self._detect_outages(cands, judgeable)

        changes: list[tuple[int, str, Tracked, bool, int | None]] = []
        staged: dict[tuple[int, str], Tracked] = {}
        for key, (state, reason, silent, net_last) in cands.items():
            if key[1] in held and silent:
                continue   # ommaviy uzilish: bu mahsulot bo'yicha jimlik hukmini to'xtatib turamiz
            prev = self.tracked.get(key)
            prev_state = prev.state if prev else None
            t, changed = apply_debounce(prev, state, reason, now_wall, s.debounce)
            staged[key] = t
            if changed:
                changes.append((key[0], key[1], t, t.state != prev_state, net_last))

        await self._write_changes(changes, alive_rows, now_wall)
        # Faqat DB'ga muvaffaqiyatli yozilgandan keyin — aks holda keyingi siklda qayta uriniladi.
        self.tracked.update(staged)
        await self._snapshot(recent, now_wall)
        await db.set_status(self.pool, "engine", {
            "last_eval": dt(now_wall).isoformat(), "reference": dt(ref).isoformat(),
            "hosts": sum(1 for h in self.hosts.values() if h.active and not h.excluded),
            "alive": len(alive_rows), "changes": len(changes), "outages": sorted(self.outages),
            "eval_ms": round((time.monotonic() - t0) * 1000),
            "sources_ok": {k: dt(v).isoformat() for k, v in self.source_ok.items()},
        })

    async def _detect_outages(self, cands, judgeable) -> set[str]:
        """Oldin OK bo'lgan hostlarning katta qismi birdan jim bo'lsa — bu server/tarmoq muammosi."""
        held: set[str] = set()
        for product, keys in judgeable.items():
            base = [k for k in keys if (t := self.tracked.get(k)) and t.state == OK]
            silent = [k for k in base if cands[k][2]]
            ratio = len(silent) / len(base) if base else 0.0
            active = len(base) >= self.s.mass_outage_min and ratio >= self.s.mass_outage_ratio
            if active:
                held.add(product)
                if product not in self.outages:
                    self.outages[product] = await self.pool.fetchval(
                        """INSERT INTO incident (kind, product, started_at, details)
                           VALUES ('mass_outage', $1, now(), $2) RETURNING id""",
                        product, {"silent": len(silent), "base": len(base), "ratio": round(ratio, 3)})
                    log.error("OMMAVIY UZILISH: %s — %d/%d host jim", product, len(silent), len(base))
            elif product in self.outages:
                await self.pool.execute("UPDATE incident SET ended_at = now() WHERE id = $1", self.outages.pop(product))
                log.info("Ommaviy uzilish tugadi: %s", product)
        return held

    async def _update_collector_incident(self) -> None:
        stale = self._collector_stale()
        if stale and self.collector_incident is None:
            self.collector_incident = await self.pool.fetchval(
                "INSERT INTO incident (kind, started_at) VALUES ('collector_stale', now()) RETURNING id")
        elif not stale and self.collector_incident is not None:
            await self.pool.execute("UPDATE incident SET ended_at = now() WHERE id = $1", self.collector_incident)
            self.collector_incident = None

    async def _write_changes(self, changes, alive_rows, now_wall: int) -> None:
        now = dt(now_wall)
        async with self.pool.acquire() as c, c.transaction():
            if changes:
                await c.executemany(
                    """INSERT INTO host_state (host_id, product, state, reason, since, last_known, last_known_reason,
                                               net_last_seen, console_last_seen, evaluated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                       ON CONFLICT (host_id, product) DO UPDATE SET state = EXCLUDED.state, reason = EXCLUDED.reason,
                           since = EXCLUDED.since, last_known = EXCLUDED.last_known,
                           last_known_reason = EXCLUDED.last_known_reason, net_last_seen = EXCLUDED.net_last_seen,
                           console_last_seen = EXCLUDED.console_last_seen, evaluated_at = EXCLUDED.evaluated_at""",
                    [(hid, p, t.state, t.reason, dt(t.since), t.last_known, t.last_known_reason, dt(net_last),
                      self._console_last_seen(hid, p), now) for hid, p, t, _, net_last in changes])
                transitions = [(hid, p, t) for hid, p, t, state_changed, _ in changes if state_changed]
                if transitions:
                    await c.executemany(
                        "UPDATE host_state_history SET ended_at = $3 WHERE host_id = $1 AND product = $2 AND ended_at IS NULL",
                        [(hid, p, now) for hid, p, _ in transitions])
                    await c.executemany(
                        "INSERT INTO host_state_history (host_id, product, state, reason, started_at) VALUES ($1, $2, $3, $4, $5)",
                        [(hid, p, t.state, t.reason, now) for hid, p, t in transitions])
            if alive_rows:
                await c.execute(
                    """UPDATE host h SET last_alive = v.la, site = v.site
                       FROM unnest($1::bigint[], $2::timestamptz[], $3::text[]) AS v(id, la, site) WHERE h.id = v.id""",
                    [r[0] for r in alive_rows], [r[1] for r in alive_rows], [r[2] for r in alive_rows])

    def _console_last_seen(self, host_id: int, product: str) -> datetime | None:
        h = self.host_by_id.get(host_id)
        r = self.consoles.get(product, {}).get(h.name) if h else None
        return r.last_seen if r else None

    async def _snapshot(self, recent: set[int], now_wall: int) -> None:
        hour = now_wall // 3600
        if hour == self.last_snapshot_hour:
            return
        counts: Counter = Counter()
        for (hid, product), t in self.tracked.items():
            if hid in recent:
                counts[(product, effective_state(t))] += 1
        ts = dt(hour * 3600)
        await self.pool.executemany(
            "INSERT INTO coverage_snapshot (ts, product, state, count) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            [(ts, p, st, n) for (p, st), n in counts.items()])
        self.last_snapshot_hour = hour


    async def maintenance(self) -> None:
        """Ma'lumotlar hajmini chegaralash: eski IP signallari, tarix va statistika."""
        before = int(time.time()) - SIGNAL_RETENTION
        pruned = self.store.prune(before)
        async with self.pool.acquire() as c:
            await c.execute("DELETE FROM ip_presence WHERE last_seen < $1", dt(before))
            await c.execute("DELETE FROM net_signal WHERE last_seen < $1", dt(before))
            await c.execute("DELETE FROM host_state_history WHERE ended_at < now() - interval '400 days'")
            await c.execute("DELETE FROM coverage_snapshot WHERE ts < now() - interval '400 days'")
            await c.execute("DELETE FROM incident WHERE ended_at < now() - interval '400 days'")
        if pruned:
            log.info("Retention: %d ta eski IP o'chirildi", pruned)


async def main() -> None:
    engine = Engine(get_settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, engine.stopping.set)
    await engine.run()
