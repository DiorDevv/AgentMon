"""Dashboard uchun ma'lumot yig'ish.

~4000 host x 4 mahsulot — bu kichik hajm: barcha qatorlar bitta so'rov bilan olinib,
filtr/saralash Python'da bajariladi va natija bir necha soniya keshlanadi.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from agentmon.config import PRODUCTS
from agentmon.model import OFFLINE, OK, PENDING, PROBLEM_STATES

UTC = timezone.utc
RECENT_DAYS = 7
CACHE_TTL = 10

STATE_LABELS = {
    "OK": "Ishlayapti",
    "UNHEALTHY": "Himoya to'liq emas",
    "STOPPED": "To'xtatilgan",
    "NOT_INSTALLED": "O'rnatilmagan",
    "NO_SIGNAL": "Signal yo'q",
    "CONFLICT": "Ziddiyat",
    "PENDING": "Tekshirilmoqda",
    "OFFLINE": "Oflayn",
}
AD_LABELS = {"STOPPED": "DC bilan aloqa yo'q", "NOT_INSTALLED": "Domenda emas", "OK": "Domenda"}


def label(product: str, state: str | None) -> str:
    if not state:
        return "—"
    if product == "ad" and state in AD_LABELS:
        return AD_LABELS[state]
    return STATE_LABELS.get(state, state)


def effective(state: str | None, last_known: str | None) -> str | None:
    if state in (OFFLINE, PENDING) and last_known:
        return last_known
    return state


@dataclass
class _Cache:
    at: float = 0.0
    rows: list[dict] | None = None
    version: object = None


_cache = _Cache()


def invalidate() -> None:
    _cache.at = 0.0


async def all_hosts(pool: asyncpg.Pool, products: tuple[str, ...] = PRODUCTS) -> list[dict]:
    # Kesh har worker'da alohida: boshqa worker hostni istisno qilgan bo'lsa (host.updated_at o'zgaradi),
    # bu arzon so'rov keshni darhol eskirgan deb topadi.
    version = await pool.fetchval("SELECT max(updated_at) FROM host")
    if _cache.rows is not None and _cache.version == version and time.monotonic() - _cache.at < CACHE_TTL:
        return _cache.rows
    rows = await pool.fetch(
        """SELECT h.id, h.display_name, h.fqdn, h.os, h.site, h.sources, h.excluded, h.note, h.last_alive,
                  coalesce((SELECT array_agg(host(ip) ORDER BY ip) FROM host_ip WHERE host_id = h.id), '{}') AS ips,
                  coalesce((SELECT jsonb_object_agg(product, jsonb_build_object(
                        'state', state, 'reason', reason, 'since', since,
                        'last_known', last_known, 'last_known_reason', last_known_reason))
                   FROM host_state WHERE host_id = h.id), '{}') AS states
           FROM host h WHERE cardinality(h.sources) > 0""")
    now = datetime.now(UTC)
    recent_after = now - timedelta(days=RECENT_DAYS)
    out = []
    for r in rows:
        states = {}
        problems = []
        for p in products:
            st = r["states"].get(p)
            if not st:
                states[p] = None
                continue
            eff = effective(st["state"], st["last_known"])
            reason = st["reason"] if eff == st["state"] else st["last_known_reason"]
            states[p] = {"state": st["state"], "eff": eff, "reason": reason, "since": st["since"]}
            if eff in PROBLEM_STATES:
                problems.append(p)
        last_alive = r["last_alive"]
        out.append({
            "id": r["id"], "name": r["display_name"], "fqdn": r["fqdn"], "os": r["os"], "site": r["site"],
            "sources": list(r["sources"]), "excluded": r["excluded"], "note": r["note"],
            "ips": list(r["ips"]), "last_alive": last_alive,
            "online": any(s and s["state"] not in (OFFLINE,) for s in states.values()),
            "recent": bool(last_alive and last_alive >= recent_after),
            "states": states, "problems": problems,
        })
    _cache.rows, _cache.at, _cache.version = out, time.monotonic(), version
    return out


def filter_hosts(rows: list[dict], *, q: str = "", product: str = "", state: str = "", site: str = "",
                 scope: str = "recent", problems_only: bool = False, include_excluded: bool = False) -> list[dict]:
    q = q.strip().lower()
    out = []
    for h in rows:
        if h["excluded"] and not include_excluded:
            continue
        if scope == "recent" and not h["recent"]:
            continue
        if scope == "online" and not h["online"]:
            continue
        if site and (h["site"] or "") != site:
            continue
        if q and q not in h["name"].lower() and not any(q in ip for ip in h["ips"]) and q not in (h["fqdn"] or "").lower():
            continue
        if problems_only and not (product in h["problems"] if product else h["problems"]):
            continue
        if product and state:
            st = h["states"].get(product)
            eff = st["eff"] if st else None
            if state == "PROBLEM":
                if eff not in PROBLEM_STATES:
                    continue
            elif eff != state:
                continue
        out.append(h)
    return out


SORTS = {
    "name": lambda h: h["name"],
    "site": lambda h: (h["site"] or "~", h["name"]),
    "last_alive": lambda h: h["last_alive"] or datetime.min.replace(tzinfo=UTC),
    "problems": lambda h: (-len(h["problems"]), h["name"]),
}


def summary(rows: list[dict], products: tuple[str, ...] = PRODUCTS) -> dict:
    scope = [h for h in rows if h["recent"] and not h["excluded"]]
    per_product = {}
    for p in products:
        counts: dict[str, int] = {}
        for h in scope:
            st = h["states"].get(p)
            key = st["eff"] if st else "NONE"
            counts[key] = counts.get(key, 0) + 1
        judged = sum(n for k, n in counts.items() if k not in (PENDING, OFFLINE, "NONE"))
        ok = counts.get(OK, 0)
        per_product[p] = {
            "counts": counts,
            "ok": ok,
            "problems": sum(counts.get(s, 0) for s in PROBLEM_STATES),
            "judged": judged,
            "coverage": round(ok / judged * 100, 1) if judged else None,
        }
    sites: dict[str, dict] = {}
    for h in scope:
        s = sites.setdefault(h["site"] or "—", {"site": h["site"] or "—", "hosts": 0, "problem_hosts": 0,
                                                **{p: 0 for p in products}})
        s["hosts"] += 1
        if h["problems"]:
            s["problem_hosts"] += 1
        for p in h["problems"]:
            s[p] += 1
    # Umumiy muvofiqlik: baholangan kompyuterlardan barcha agentlari joyida bo'lganlari ulushi.
    judged_hosts = [h for h in scope
                    if any(st and st["eff"] not in (PENDING, OFFLINE) for st in h["states"].values())]
    compliant = sum(1 for h in judged_hosts if not h["problems"])
    return {
        "compliant": compliant,
        "judged_hosts": len(judged_hosts),
        "compliance": round(compliant / len(judged_hosts) * 100, 1) if judged_hosts else None,
        "hosts_total": sum(1 for h in rows if not h["excluded"]),
        "hosts_recent": len(scope),
        "hosts_online": sum(1 for h in scope if h["online"]),
        "hosts_with_problems": sum(1 for h in scope if h["problems"]),
        "products": per_product,
        "sites": sorted(sites.values(), key=lambda s: (-s["problem_hosts"], s["site"])),
    }
