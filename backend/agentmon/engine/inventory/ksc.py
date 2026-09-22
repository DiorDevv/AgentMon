"""Kaspersky Security Center OpenAPI (odatda https://<ksc>:13299).

Oqim: login -> HostGroup.FindHosts (accessor) -> ChunkAccessor.GetItemsChunk (sahifalab) -> Release.
"""

from __future__ import annotations

import base64
import ipaddress
from datetime import datetime, timedelta, timezone

import httpx

from agentmon.config import Settings, ksc_verify
from agentmon.engine.inventory import http
from agentmon.model import ConsoleRecord, dedupe_latest, norm_host

CHUNK = 500

FIELDS = [
    "KLHST_WKS_DN", "KLHST_WKS_FQDN", "KLHST_WKS_WINHOSTNAME", "KLHST_WKS_IP_LONG",
    "KLHST_WKS_STATUS", "KLHST_WKS_STATUS_ID", "KLHST_WKS_RTP_STATE", "KLHST_WKS_LAST_VISIBLE",
    "KLHST_WKS_LAST_NAGENT_CONNECTED",
    "KLHST_WKS_LAST_UPDATE", "KLHST_WKS_OS_NAME", "KLHST_WKS_NAG_VERSION", "KLHST_WKS_GROUPID",
]

# KLHST_WKS_STATUS bitlari
ST_NAGENT_INSTALLED = 0x04
ST_NAGENT_ALIVE = 0x08
ST_RTP_INSTALLED = 0x10

# KLHST_WKS_RTP_STATE
RTP_RUNNING = {3, 4, 5, 6, 7, 8}
RTP_NAMES = {0: "noma'lum", 1: "to'xtatilgan", 2: "pauza qilingan", 9: "xatolik"}


def _unwrap(v):
    """KSC qiymatlari ko'pincha {"type": "datetime", "value": ...} ko'rinishida keladi."""
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def _dt(v) -> datetime | None:
    v = _unwrap(v)
    if not v or not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ip(v) -> str | None:
    v = _unwrap(v)
    if not isinstance(v, int) or v == 0:
        return None
    ip = ipaddress.IPv4Address(v & 0xFFFFFFFF)
    if not ip.is_private:
        # Bayt tartibi teskari bo'lishi mumkin — xususiy manzil chiqsa o'shani olamiz.
        rev = ipaddress.IPv4Address(ip.packed[::-1])
        if rev.is_private:
            ip = rev
    return str(ip)


def to_record(h: dict, now: datetime, bases_max_age: timedelta) -> ConsoleRecord | None:
    h = {k: _unwrap(v) for k, v in h.items()}
    display = h.get("KLHST_WKS_WINHOSTNAME") or h.get("KLHST_WKS_DN") or h.get("KLHST_WKS_FQDN")
    # To'liq nom (FQDN) afzal: WINHOSTNAME 15 belgiga qisqartirilgan NetBIOS nomi.
    name = norm_host(h.get("KLHST_WKS_FQDN")) or norm_host(display)
    status = int(h.get("KLHST_WKS_STATUS") or 0)
    # Network Agent yo'q hostlar (masalan, tarmoq skani orqali topilganlar) — agent o'rnatilmagan.
    if not name or not status & ST_NAGENT_INSTALLED:
        return None

    rtp = h.get("KLHST_WKS_RTP_STATE")
    rtp = int(rtp) if rtp is not None else 0
    bases = _dt(h.get("KLHST_WKS_LAST_UPDATE"))
    reason = None
    if not status & ST_RTP_INSTALLED:
        reason = "Faqat Network Agent bor, antivirus (KES) o'rnatilmagan"
    elif rtp not in RTP_RUNNING:
        reason = f"Real-time himoya {RTP_NAMES.get(rtp, f'holati: {rtp}')}"
    elif bases is not None and now - bases > bases_max_age:
        reason = f"Antivirus bazalari eskirgan ({(now - bases).days} kun)"

    ip = _ip(h.get("KLHST_WKS_IP_LONG"))
    return ConsoleRecord(
        product="ksc", name=name, display_name=display or name, healthy=reason is None, reason=reason,
        fqdn=(h.get("KLHST_WKS_FQDN") or None), os=h.get("KLHST_WKS_OS_NAME"),
        ips=[ip] if ip else [],
        last_seen=_dt(h.get("KLHST_WKS_LAST_NAGENT_CONNECTED")) or _dt(h.get("KLHST_WKS_LAST_VISIBLE")),
        version=h.get("KLHST_WKS_NAG_VERSION"),
        details={
            "status_id": h.get("KLHST_WKS_STATUS_ID"),
            "rtp_state": rtp,
            "nagent_alive": bool(status & ST_NAGENT_ALIVE),
            "bases_updated": bases.isoformat() if bases else None,
            "group_id": h.get("KLHST_WKS_GROUPID"),
        },
    )


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


async def fetch(s: Settings) -> list[ConsoleRecord]:
    base = s.ksc_url.rstrip("/") + "/api/v1.0/"
    auth = (f'KSCBasic user="{_b64(s.ksc_user)}", pass="{_b64(s.ksc_password)}", '
            f'domain="{_b64(s.ksc_domain)}", internal="{"1" if s.ksc_internal_user else "0"}"')
    now = datetime.now(timezone.utc)
    max_age = timedelta(hours=s.ksc_bases_max_age_hours)

    # KSC — ichki server: .env dagi HTTPS_PROXY (Cortex uchun) unga hech qachon qo'llanmasin.
    async with httpx.AsyncClient(verify=ksc_verify(s), timeout=120, trust_env=False,
                                 headers={"Content-Type": "application/json"}) as client:
        await http.post(client, base + "login", headers={"Authorization": auth})

        async def call(method: str, params: dict) -> dict:
            resp = await http.post(client, base + method, json=params)
            data = resp.json()
            if isinstance(data, dict) and data.get("PxgError"):
                raise RuntimeError(f"KSC {method}: {data['PxgError']}")
            return data

        found = await call("HostGroup.FindHosts", {
            "wstrFilter": s.ksc_filter, "vecFieldsToReturn": FIELDS, "vecFieldsToOrder": [],
            "pParams": {"KLSRVH_SLAVE_REC_DEPTH": 0, "KLGRP_FIND_FROM_CUR_VS_ONLY": False},
            "lMaxLifeTime": 1200,
        })
        accessor = found["strAccessor"]
        records: list[ConsoleRecord] = []
        try:
            total = int((await call("ChunkAccessor.GetItemsCount", {"strAccessor": accessor}))["PxgRetVal"])
            for start in range(0, total, CHUNK):
                chunk = await call("ChunkAccessor.GetItemsChunk",
                                   {"strAccessor": accessor, "nStart": start, "nCount": CHUNK})
                items = (chunk.get("pChunk") or {}).get("KLCSP_ITERATOR_ARRAY") or []
                for item in items:
                    rec = to_record(_unwrap(item) or {}, now, max_age)
                    if rec:
                        records.append(rec)
        finally:
            try:
                await call("ChunkAccessor.Release", {"strAccessor": accessor})
            except Exception:  # noqa: BLE001 — tozalash xatosi asosiy natijaga ta'sir qilmasin
                pass
    return dedupe_latest(records)
