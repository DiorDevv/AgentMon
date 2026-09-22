from __future__ import annotations

import csv
import io
import ipaddress
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import ORJSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentmon import db
from agentmon.api import queries
from agentmon.api.auth import COOKIE, authenticate, check_config, current_user, issue_cookie, rate_limited
from agentmon.config import Settings, get_settings, parse_endpoints, parse_subnets

UTC = timezone.utc
log = logging.getLogger("agentmon.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    check_config(s)   # noto'g'ri sozlama bo'lsa — darhol va aniq xato, har so'rovda 500 emas
    if s.web_auth == "none":
        log.warning("WEB_AUTH=none — interfeys autentifikatsiyasiz ochiq! Faqat test uchun.")
    app.state.pool = await db.create_pool(s.database_url, min_size=1, max_size=5)
    await db.migrate(app.state.pool)
    yield
    await app.state.pool.close()


app = FastAPI(title="AgentMon", lifespan=lifespan, default_response_class=ORJSONResponse,
              docs_url=None, redoc_url=None, openapi_url=None)

User = Annotated[str, Depends(current_user)]


def pool(request: Request):
    return request.app.state.pool


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=256)


@app.post("/api/login")
async def login(body: LoginIn, request: Request, response: Response, s: Settings = Depends(get_settings)):
    client = request.client.host if request.client else "?"
    if rate_limited(f"{client}:{body.username.lower()}"):
        raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
    user = await authenticate(s, body.username.strip(), body.password)
    if not user:
        raise HTTPException(401, "Login yoki parol noto'g'ri, yoki ruxsat yo'q")
    response.set_cookie(COOKIE, issue_cookie(s, user), max_age=s.web_session_hours * 3600, httponly=True,
                        samesite="strict", secure=s.web_cookie_secure, path="/")
    return {"user": user}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(user: User, s: Settings = Depends(get_settings)):
    return {"user": user, "auth": s.web_auth, "products": list(s.products)}


# ------------------------------------------------------------------ dashboard
@app.get("/api/summary")
async def summary(request: Request, user: User, s: Settings = Depends(get_settings)):
    p = pool(request)
    rows = await queries.all_hosts(p, s.products)
    out = queries.summary(rows, s.products)
    out["unknown_devices"] = await p.fetchval(
        """SELECT count(*) FROM ip_presence p
           WHERE p.last_seen > now() - interval '24 hours'
             AND NOT EXISTS (SELECT 1 FROM host_ip h WHERE h.ip = p.ip)
             AND NOT EXISTS (SELECT 1 FROM ip_ignore i WHERE i.ip = p.ip)""")
    out["incidents"] = [dict(r) for r in await p.fetch(
        "SELECT id, kind, product, started_at, details FROM incident WHERE ended_at IS NULL ORDER BY started_at")]
    out["status"] = await _status(p)
    return out


async def _status(p) -> dict:
    rows = await p.fetch("SELECT key, value, updated_at FROM system_status")
    return {r["key"]: {**r["value"], "updated_at": r["updated_at"]} for r in rows}


@app.get("/api/events")
async def events(request: Request, user: User, limit: int = Query(30, ge=1, le=200), days: int = Query(7, ge=1, le=90)):
    """So'nggi haqiqiy o'zgarishlar: yangi muammolar va tiklanishlar.

    Oflayn/tekshirilmoqda o'tishlari shovqin — ular o'tkazib yuboriladi. Host qayta yoqilib, avvalgi
    holatiga qaytgani ham hodisa emas (oldingi "haqiqiy" holat bilan solishtiriladi).
    """
    rows = await pool(request).fetch(
        """SELECT e.host_id, h.display_name, h.site, e.product, e.state, e.reason, e.started_at,
                  prev.state AS prev_state
           FROM host_state_history e
           JOIN host h ON h.id = e.host_id
           LEFT JOIN LATERAL (
               SELECT p.state FROM host_state_history p
               WHERE p.host_id = e.host_id AND p.product = e.product AND p.started_at < e.started_at
                 AND p.state NOT IN ('OFFLINE', 'PENDING')
               ORDER BY p.started_at DESC LIMIT 1) prev ON true
           WHERE e.started_at > now() - make_interval(days => $2)
             AND e.state NOT IN ('OFFLINE', 'PENDING')
             AND e.state IS DISTINCT FROM prev.state
             AND (e.state <> 'OK' OR prev.state IS NOT NULL)
             AND NOT h.excluded
           ORDER BY e.started_at DESC LIMIT $1""", limit, days)
    return [dict(r) for r in rows]


@app.get("/api/trend")
async def trend(request: Request, user: User, days: int = Query(30, ge=1, le=365),
                s: Settings = Depends(get_settings)):
    rows = await pool(request).fetch(
        "SELECT ts, product, state, count FROM coverage_snapshot WHERE ts > now() - make_interval(days => $1) ORDER BY ts",
        days)
    series: dict[str, dict] = {p: {} for p in s.products}
    for r in rows:
        if r["product"] not in series:
            continue
        pt = series[r["product"]].setdefault(r["ts"], {"ts": r["ts"], "ok": 0, "problems": 0})
        if r["state"] == "OK":
            pt["ok"] += r["count"]
        elif r["state"] in queries.PROBLEM_STATES:
            pt["problems"] += r["count"]
    out = {}
    for p, pts in series.items():
        lst = []
        for pt in pts.values():
            judged = pt["ok"] + pt["problems"]
            lst.append({**pt, "coverage": round(pt["ok"] / judged * 100, 2) if judged else None})
        out[p] = lst
    return out


# ------------------------------------------------------------------ hosts
class HostFilter(BaseModel):
    q: str = ""
    product: str = ""
    state: str = ""
    site: str = ""
    scope: str = "recent"
    problems: bool = False
    excluded: bool = False
    sort: str = "problems"


def _csv_safe(v: str) -> str:
    """Excel formula injection'dan himoya: '=', '+', '-', '@' bilan boshlangan qiymatlar matn sifatida."""
    return "'" + v if v and v[0] in "=+-@\t\r" else v


def host_filter(q: str = "", product: str = "", state: str = "", site: str = "", scope: str = "recent",
                problems: bool = False, excluded: bool = False, sort: str = "problems",
                s: Settings = Depends(get_settings)) -> HostFilter:
    if product and product not in s.products:
        raise HTTPException(400, "Noma'lum mahsulot")
    return HostFilter(q=q, product=product, state=state, site=site, scope=scope, problems=problems,
                      excluded=excluded, sort=sort if sort in queries.SORTS else "problems")


async def _filtered(request: Request, f: HostFilter) -> list[dict]:
    rows = await queries.all_hosts(pool(request), get_settings().products)
    out = queries.filter_hosts(rows, q=f.q, product=f.product, state=f.state, site=f.site, scope=f.scope,
                               problems_only=f.problems, include_excluded=f.excluded)
    return sorted(out, key=queries.SORTS[f.sort], reverse=f.sort == "last_alive")


@app.get("/api/hosts")
async def hosts(request: Request, user: User, f: HostFilter = Depends(host_filter),
                page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500)):
    rows = await _filtered(request, f)
    start = (page - 1) * size
    return {"total": len(rows), "page": page, "size": size, "items": rows[start:start + size]}


@app.get("/api/hosts.csv")
async def hosts_csv(request: Request, user: User, f: HostFilter = Depends(host_filter),
                    s: Settings = Depends(get_settings)):
    rows = await _filtered(request, f)
    buf = io.StringIO()
    buf.write("﻿")  # Excel UTF-8 ni to'g'ri ochishi uchun
    w = csv.writer(buf, delimiter=";")
    names = {"ad": "AD", "cortex": "Cortex XDR", "ksc": "Kaspersky", "si": "SearchInform"}
    w.writerow(["Host", "FQDN", "IP", "Sayt", "OS", "Oxirgi faollik", *(names[p] for p in s.products), "Izoh"])
    for h in rows:
        reasons = []
        cols = []
        for p in s.products:
            st = h["states"].get(p)
            cols.append(queries.label(p, st["eff"] if st else None))
            if st and st["reason"] and st["eff"] != "OK":
                reasons.append(f"{p.upper()}: {st['reason']}")
        la = h["last_alive"].astimezone().strftime("%Y-%m-%d %H:%M") if h["last_alive"] else ""
        w.writerow([_csv_safe(v) for v in (h["name"], h["fqdn"] or "", ", ".join(h["ips"]), h["site"] or "",
                                           h["os"] or "", la, *cols, " | ".join(reasons))])
    name = f"agentmon-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/sites")
async def sites(request: Request, user: User):
    rows = await pool(request).fetch("SELECT DISTINCT site FROM host WHERE site IS NOT NULL ORDER BY site")
    return [r["site"] for r in rows]


@app.get("/api/hosts/{host_id}")
async def host_detail(host_id: int, request: Request, user: User, s: Settings = Depends(get_settings)):
    p = pool(request)
    h = await p.fetchrow("SELECT * FROM host WHERE id = $1", host_id)
    if not h:
        raise HTTPException(404, "Host topilmadi")
    ips = [dict(r) for r in await p.fetch(
        """SELECT host(i.ip) AS ip, i.source, i.observed_at, i.alternatives,
                  pr.site, pr.first_seen, pr.alive_since, pr.last_seen
           FROM host_ip i LEFT JOIN ip_presence pr ON pr.ip = i.ip
           WHERE i.host_id = $1 ORDER BY pr.last_seen DESC NULLS LAST""", host_id)]
    net = {r["grp"]: r["last_seen"] for r in await p.fetch(
        """SELECT grp, max(last_seen) AS last_seen FROM net_signal
           WHERE ip IN (SELECT ip FROM host_ip WHERE host_id = $1) GROUP BY grp""", host_id)}
    states = {r["product"]: dict(r) for r in await p.fetch("SELECT * FROM host_state WHERE host_id = $1", host_id)}
    consoles = {r["product"]: dict(r) for r in await p.fetch(
        """SELECT product, display_name, os, array(SELECT host(x) FROM unnest(ips) x) AS ips, last_seen,
                  healthy, reason, version, details, synced_at
           FROM console_endpoint WHERE name = $1""", h["name"])}
    history = [dict(r) for r in await p.fetch(
        """SELECT product, state, reason, started_at, ended_at FROM host_state_history
           WHERE host_id = $1 ORDER BY started_at DESC LIMIT 200""", host_id)]
    products = {}
    for prod in s.products:
        st = states.get(prod)
        products[prod] = {
            "state": st["state"] if st else None,
            "eff": queries.effective(st["state"], st["last_known"]) if st else None,
            "reason": st["reason"] if st else None,
            "since": st["since"] if st else None,
            "last_known": st["last_known"] if st else None,
            "last_known_reason": st["last_known_reason"] if st else None,
            "net_last_seen": net.get(prod),
            "net_denied": net.get(prod + "!"),
            "console": consoles.get(prod),
        }
    return {
        "host": {k: h[k] for k in ("id", "display_name", "fqdn", "os", "site", "sources", "excluded", "note",
                                   "first_seen", "last_alive")},
        "net_any": net.get("any") or max((i["last_seen"] for i in ips if i["last_seen"]), default=None),
        "ips": ips, "products": products, "history": history,
    }


class ExcludeIn(BaseModel):
    excluded: bool
    note: str | None = Field(None, max_length=500)


@app.post("/api/hosts/{host_id}/exclude")
async def host_exclude(host_id: int, body: ExcludeIn, request: Request, user: User):
    res = await pool(request).execute(
        "UPDATE host SET excluded = $2, note = $3, updated_at = now() WHERE id = $1",
        host_id, body.excluded, (body.note or "").strip() or None)
    if res.endswith(" 0"):
        raise HTTPException(404, "Host topilmadi")
    queries.invalidate()
    return {"ok": True}


# ------------------------------------------------------------------ unknown devices
@app.get("/api/unknown")
async def unknown(request: Request, user: User, hours: int = Query(24, ge=1, le=720)):
    rows = await pool(request).fetch(
        """SELECT host(p.ip) AS ip, p.site, p.first_seen, p.alive_since, p.last_seen,
                  coalesce((SELECT jsonb_object_agg(grp, last_seen) FROM net_signal s
                            WHERE s.ip = p.ip AND s.last_seen > now() - interval '24 hours'), '{}') AS signals,
                  (SELECT d.fqdn FROM dns_record d WHERE d.ip = p.ip
                   ORDER BY d.observed_at DESC NULLS LAST LIMIT 1) AS dns_name
           FROM ip_presence p
           WHERE p.last_seen > now() - make_interval(hours => $1)
             AND NOT EXISTS (SELECT 1 FROM host_ip h WHERE h.ip = p.ip)
             AND NOT EXISTS (SELECT 1 FROM ip_ignore i WHERE i.ip = p.ip)
           ORDER BY p.last_seen DESC LIMIT 5000""", hours)
    return [dict(r) for r in rows]


class IgnoreIn(BaseModel):
    ip: str
    note: str | None = Field(None, max_length=500)


@app.post("/api/unknown/ignore")
async def ignore_ip(body: IgnoreIn, request: Request, user: User):
    try:
        ip = str(ipaddress.ip_address(body.ip))
    except ValueError:
        raise HTTPException(400, "IP noto'g'ri") from None
    await pool(request).execute(
        """INSERT INTO ip_ignore (ip, note, created_by) VALUES ($1::inet, $2, $3)
           ON CONFLICT (ip) DO UPDATE SET note = EXCLUDED.note, created_by = EXCLUDED.created_by""",
        ip, (body.note or "").strip() or None, user)
    return {"ok": True}


@app.get("/api/unknown/ignored")
async def ignored(request: Request, user: User):
    return [dict(r) for r in await pool(request).fetch(
        "SELECT host(ip) AS ip, note, created_by, created_at FROM ip_ignore ORDER BY created_at DESC")]


@app.delete("/api/unknown/ignore/{ip}")
async def unignore_ip(ip: str, request: Request, user: User):
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        raise HTTPException(400, "IP noto'g'ri") from None
    await pool(request).execute("DELETE FROM ip_ignore WHERE ip = $1::inet", ip)
    return {"ok": True}


# ------------------------------------------------------------------ system
@app.get("/api/system")
async def system(request: Request, user: User, s: Settings = Depends(get_settings)):
    p = pool(request)
    return {
        "status": await _status(p),
        "sources": [dict(r) for r in await p.fetch("SELECT * FROM source_status ORDER BY source")],
        "incidents": [dict(r) for r in await p.fetch(
            """SELECT id, kind, product, started_at, ended_at, details FROM incident
               WHERE ended_at IS NULL OR ended_at > now() - interval '30 days' ORDER BY started_at DESC LIMIT 100""")],
        "subnets": _merge_subnets(
            [dict(r) for r in await p.fetch("SELECT cidr::text AS cidr, site, source FROM net_subnet ORDER BY cidr")],
            s),
        "dcs": [dict(r) for r in await p.fetch("SELECT host(ip) AS ip, name FROM dc_server ORDER BY name")],
        "config": {
            "targets": {
                "cortex": [f"{ip}:{port}" for ip, port in parse_endpoints(s.target_cortex)],
                "ksc": [f"{ip}:{port}" for ip, port in parse_endpoints(s.target_ksc)],
                "si": [f"{ip}:{port}" for ip, port in parse_endpoints(s.target_si)],
                "ad_ports": s.dc_ports,
            },
            "thresholds": {p: v for p, v in s.thresholds.items() if p in s.products},
            "products": list(s.products),
            "alive_window": s.alive_window, "grace": s.grace, "debounce": s.debounce,
            "mass_outage_ratio": s.mass_outage_ratio, "mass_outage_min": s.mass_outage_min,
            "user_subnets_config": s.user_subnets, "exclude_subnets": s.exclude_subnets,
        },
        "now": datetime.now(UTC),
    }


def _merge_subnets(ad_rows: list[dict], s: Settings) -> list[dict]:
    rows = {r["cidr"]: r for r in ad_rows}
    for cidr, site in parse_subnets(s.user_subnets):
        try:
            key = str(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
        rows[key] = {"cidr": key, "site": site or rows.get(key, {}).get("site", ""), "source": "config"}
    return sorted(rows.values(), key=lambda r: ipaddress.ip_network(r["cidr"]))


@app.get("/api/health")
async def health(request: Request):
    await pool(request).fetchval("SELECT 1")
    return {"ok": True}
