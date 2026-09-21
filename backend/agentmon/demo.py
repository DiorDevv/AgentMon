"""Demo ma'lumotlar: haqiqiy Engine kodini soxta 4000 host muhitida ishga tushiradi.

Maqsad — real manbalar ulanishidan oldin interfeysni ko'rish va butun zanjirni tekshirish.
FAQAT bo'sh (yoki test) bazada ishlating:

    docker compose run --rm engine python -m agentmon.demo --yes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from agentmon.config import get_settings
from agentmon.engine.app import Engine, dt
from agentmon.engine.identity import Candidate
from agentmon.engine.signals import Presence
from agentmon.model import ConsoleRecord

UTC = timezone.utc
SITES = [("10.10.0.0/16", "Markaz", 0.55), ("10.20.0.0/16", "Filial-Samarqand", 0.12),
         ("10.30.0.0/16", "Filial-Buxoro", 0.11), ("10.40.0.0/16", "Filial-Namangan", 0.12),
         ("10.50.0.0/16", "Filial-Andijon", 0.10)]
DC_IP = "172.25.10.10"
N_UNKNOWN = 37


def build_plan(n: int) -> list[tuple[str, str, str, dict]]:
    """Deterministik demo tarmoq: (nom, ip, sayt, ssenariy). Seed va jonli trafik bir xil rejadan foydalanadi."""
    rnd = random.Random(42)
    plan = []
    counters = {i: 0 for i in range(len(SITES))}
    for _ in range(n):
        si = rnd.choices(range(len(SITES)), weights=[w for *_, w in SITES])[0]
        k = counters[si]
        counters[si] += 1
        ip = f"10.{10 * (si + 1)}.{k // 250}.{k % 250 + 1}"
        name = f"{['tash', 'sam', 'bux', 'nam', 'and'][si]}-pc-{k:04d}"
        f = {
            "offline": rnd.random() < 0.09,
            "no_ad": rnd.random() < 0.006,
            "cx": rnd.choices(["ok", "missing", "stopped", "partial"], [0.955, 0.025, 0.012, 0.008])[0],
            "ksc": rnd.choices(["ok", "missing", "stopped", "rtp", "bases", "nokes"],
                               [0.94, 0.012, 0.01, 0.018, 0.012, 0.008])[0],
            "si": rnd.choices(["ok", "silent"], [0.975, 0.025])[0],
        }
        plan.append((name, ip, SITES[si][1], f))
    return plan


def unknown_devices() -> list[tuple[str, str, bool]]:
    """Inventarda yo'q, lekin tarmoqda faol qurilmalar: (ip, sayt, DC trafigi bormi)."""
    rnd = random.Random(43)
    out = []
    for j in range(N_UNKNOWN):
        si = rnd.randrange(len(SITES))
        out.append((f"10.{10 * (si + 1)}.{200 + j // 200}.{j % 200 + 1}", SITES[si][1], j % 5 == 0))
    return out


def signal_groups(f: dict) -> list[str]:
    """Ssenariy bo'yicha host qaysi serverlarga murojaat qiladi."""
    groups = [] if f["no_ad"] else ["ad"]
    if f["cx"] in ("ok", "partial"):
        groups.append("cortex")
    if f["ksc"] in ("ok", "rtp", "bases", "nokes"):
        groups.append("ksc")
    if f["si"] == "ok":
        groups.append("si")
    return groups


async def seed(n: int, force: bool) -> None:
    s = get_settings()
    eng = Engine(s)
    await eng.start()
    p = eng.pool
    if await p.fetchval("SELECT count(*) FROM host") and not force:
        raise SystemExit("Bazada allaqachon hostlar bor. Davom etish uchun --force (faqat test bazasi!)")

    rnd = random.Random(42)
    now = int(time.time())
    nowdt = dt(now)

    await p.execute("DELETE FROM net_subnet; DELETE FROM dc_server")
    await p.executemany("INSERT INTO net_subnet (cidr, site, source) VALUES ($1::cidr, $2, 'ad')",
                        [(c, name) for c, name, _ in SITES])
    await p.execute("INSERT INTO dc_server (ip, name) VALUES ($1::inet, 'dc01.corp.local')", DC_IP)
    await eng._rebuild_classifier()

    ad, cx, ks, dns = [], [], [], []
    plan = build_plan(n)
    for name, ip, _site, f in plan:
        ls = nowdt - timedelta(minutes=rnd.randint(1, 20))
        if not f["no_ad"]:
            ad.append(ConsoleRecord("ad", name, name.upper(), True, fqdn=f"{name}.corp.local", os="Windows 11 Pro",
                                    last_seen=nowdt - timedelta(days=rnd.randint(0, 10)),
                                    details={"enabled": True, "stale": False}))
            dns.append(Candidate(ip, name, "dns", now - rnd.randint(600, 86400)))
        if f["cx"] != "missing":
            stopped = f["cx"] == "stopped"
            cx.append(ConsoleRecord(
                "cortex", name, name.upper(), f["cx"] != "partial",
                reason="Cortex qisman himoyada (ba'zi modullar ishlamayapti)" if f["cx"] == "partial" else None,
                os="Windows 11", ips=[ip], version="8.5.1.1234",
                last_seen=nowdt - timedelta(days=rnd.randint(2, 20)) if stopped else ls,
                details={"endpoint_status": "DISCONNECTED" if stopped else "CONNECTED",
                         "operational_status": "PARTIALLY_PROTECTED" if f["cx"] == "partial" else "PROTECTED"}))
        if f["ksc"] != "missing":
            reason = {"rtp": "Real-time himoya to'xtatilgan", "bases": "Antivirus bazalari eskirgan (6 kun)",
                      "nokes": "Faqat Network Agent bor, antivirus (KES) o'rnatilmagan"}.get(f["ksc"])
            ks.append(ConsoleRecord(
                "ksc", name, name.upper(), reason is None, reason=reason, os="Windows 11", ips=[ip],
                version="14.2.0.123",
                last_seen=nowdt - timedelta(days=rnd.randint(3, 30)) if f["ksc"] == "stopped" else ls,
                details={"rtp_state": 1 if f["ksc"] == "rtp" else 4}))

    for product, recs in (("ad", ad), ("cortex", cx), ("ksc", ks)):
        await eng._replace_console(product, recs)
        eng.source_ok[product] = now
        await p.execute(
            """INSERT INTO source_status (source, last_attempt, last_success, item_count) VALUES ($1, now(), now(), $2)
               ON CONFLICT (source) DO UPDATE SET last_attempt = now(), last_success = now(), item_count = $2,
               last_error = NULL""", product, len(recs))
    eng.dns = dns
    await eng._rebuild_hosts()

    # --- tarmoq signallari
    st = eng.store

    def put(ip: str, site: str, alive_since: int, last: int, groups: dict[str, int]) -> None:
        st.presence[ip] = Presence(site, now - 30 * 86400, alive_since, last)
        st.dirty_presence.add(ip)
        for g, t in groups.items():
            st.last[(ip, g)] = t
            st.dirty_signal.add((ip, g))

    for _name, ip, site, f in plan:
        groups = {g: now - rnd.randint(10, 300) for g in signal_groups(f)}
        put(ip, site, now - rnd.randint(5, 9) * 3600, now - rnd.randint(5, 120), groups)

    # Noma'lum qurilmalar: inventarda yo'q, lekin tarmoqda faol.
    for ip, site, has_dc in unknown_devices():
        put(ip, site, now - rnd.randint(1, 20) * 3600, now - rnd.randint(5, 300), {"ad": now - 300} if has_dc else {})

    st.stats.last_rx_wall = time.time()
    st.stats.watermark = now
    eng.data_since = time.time() - 3600   # isinish davri o'tgan deb hisoblaymiz
    st.stats.received = 48_211_907
    st.stats.accepted = 31_904_112
    st.stats.last_update_event = now - 30
    st.stats.by_event.update({1: 15_020_331, 2: 14_998_120, 3: 402_011, 5: 17_791_445})
    await eng.flush()
    await eng.evaluate_cycle()

    # --- ikkinchi bosqich: ba'zi hostlar oflayn bo'ladi (oxirgi ma'lum holat saqlanadi)
    for _name, ip, _site, f in plan:
        if f["offline"]:
            pr = st.presence[ip]
            pr.last_seen = now - rnd.randint(2, 140) * 3600
            st.dirty_presence.add(ip)
    await eng.flush()
    await eng.evaluate_cycle()

    await _backdate(p, rnd, nowdt)
    await _trend(p, rnd, nowdt)
    await p.execute(
        """INSERT INTO incident (kind, product, started_at, ended_at, details)
           VALUES ('mass_outage', 'cortex', $1, $2, $3)""",
        nowdt - timedelta(days=9, hours=3), nowdt - timedelta(days=9, hours=2, minutes=18),
        {"silent": 3120, "base": 3390, "ratio": 0.92})
    await eng.close()
    print(f"Demo tayyor: {n} host, {len(plan) - len(ad)} ta AD'siz, {N_UNKNOWN} noma'lum qurilma")


async def _backdate(p, rnd: random.Random, nowdt: datetime) -> None:
    """Muammolar "bugun paydo bo'lgan"dek ko'rinmasligi uchun tarixni orqaga suramiz."""
    rows = await p.fetch("SELECT host_id, product, state FROM host_state")
    upd, hist = [], []
    for r in rows:
        since = nowdt - timedelta(hours=rnd.randint(2, 24 * 25))
        upd.append((r["host_id"], r["product"], since))
        if r["state"] != "OK":
            hist.append((r["host_id"], r["product"], "OK", None, since - timedelta(days=rnd.randint(5, 60)), since))
    await p.executemany("UPDATE host_state SET since = $3 WHERE host_id = $1 AND product = $2", upd)
    await p.executemany("UPDATE host_state_history SET started_at = $3 WHERE host_id = $1 AND product = $2", upd)
    await p.executemany(
        """INSERT INTO host_state_history (host_id, product, state, reason, started_at, ended_at)
           VALUES ($1, $2, $3, $4, $5, $6)""", hist)


async def _trend(p, rnd: random.Random, nowdt: datetime) -> None:
    cur = {(r["product"], r["state"]): r["count"] for r in await p.fetch(
        "SELECT product, state, count FROM coverage_snapshot WHERE ts = (SELECT max(ts) FROM coverage_snapshot)")}
    rows = []
    hour0 = nowdt.replace(minute=0, second=0, microsecond=0)
    for h in range(1, 30 * 24):
        ts = hour0 - timedelta(hours=h)
        drift = h / (30 * 24)          # o'tmishda muammolar ko'proq bo'lgan — jamoa tuzatib boryapti
        for (product, state), count in cur.items():
            if state == "OK":
                c = count - int(count * 0.06 * drift) + rnd.randint(-8, 8)
            elif state in ("OFFLINE", "PENDING"):
                c = count + rnd.randint(-20, 20)
            else:
                c = int(count * (1 + 2.2 * drift)) + rnd.randint(-2, 2)
            rows.append((ts, product, state, max(0, c)))
    await p.executemany(
        "INSERT INTO coverage_snapshot (ts, product, state, count) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING", rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hosts", type=int, default=4000)
    ap.add_argument("--yes", action="store_true", help="demo ma'lumot yozishni tasdiqlash")
    ap.add_argument("--force", action="store_true", help="baza bo'sh bo'lmasa ham yozish")
    a = ap.parse_args()
    if not a.yes:
        raise SystemExit("Bu buyruq bazaga soxta ma'lumot yozadi. Tasdiqlash uchun --yes qo'shing.")
    asyncio.run(seed(a.hosts, a.force))
