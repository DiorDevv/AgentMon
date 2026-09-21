"""Cortex XDR Public API: endpoint'lar ro'yxati (read-only kalit yetarli)."""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timezone

import httpx

from agentmon.config import Settings
from agentmon.engine.inventory import http
from agentmon.model import ConsoleRecord, dedupe_latest, norm_host

PAGE = 100  # API cheklovi: bir so'rovda maksimum 100 ta

_UNHEALTHY_OPS = {
    "PARTIALLY_PROTECTED": "Cortex qisman himoyada (ba'zi modullar ishlamayapti)",
    "UNPROTECTED": "Cortex himoyasiz holatda",
}


def _headers(s: Settings) -> dict[str, str]:
    if s.cortex_key_type.lower() == "advanced":
        nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
        ts = str(int(datetime.now(timezone.utc).timestamp()) * 1000)
        digest = hashlib.sha256(f"{s.cortex_key}{nonce}{ts}".encode()).hexdigest()
        return {"x-xdr-timestamp": ts, "x-xdr-nonce": nonce, "x-xdr-auth-id": s.cortex_key_id,
                "Authorization": digest, "Content-Type": "application/json"}
    return {"x-xdr-auth-id": s.cortex_key_id, "Authorization": s.cortex_key,
            "Content-Type": "application/json"}


def to_record(ep: dict) -> ConsoleRecord | None:
    status = (ep.get("endpoint_status") or "").upper()
    name = norm_host(ep.get("endpoint_name") or ep.get("host_name"))
    if not name or status == "UNINSTALLED":
        return None
    op = (ep.get("operational_status") or "").upper()
    reason = _UNHEALTHY_OPS.get(op)
    ls = ep.get("last_seen")
    ips = ep.get("ip") or []
    if isinstance(ips, str):
        ips = [ips]
    return ConsoleRecord(
        product="cortex", name=name, display_name=ep.get("endpoint_name") or name,
        healthy=reason is None, reason=reason,
        os=ep.get("os_version") or ep.get("os_type"),
        ips=[ip for ip in ips if ip and ":" not in ip],
        last_seen=datetime.fromtimestamp(ls / 1000, timezone.utc) if ls else None,
        version=ep.get("agent_version"),
        details={
            "endpoint_id": ep.get("endpoint_id"),
            "endpoint_status": status,
            "operational_status": op or None,
            "endpoint_type": ep.get("endpoint_type"),
            "content_version": ep.get("content_version"),
            "is_isolated": ep.get("is_isolated"),
            "domain": ep.get("domain"),
        },
    )


async def fetch(s: Settings) -> list[ConsoleRecord]:
    url = f"https://{s.cortex_fqdn}/public_api/v1/endpoints/get_endpoint/"
    records: list[ConsoleRecord] = []
    async with httpx.AsyncClient(timeout=60) as client:
        start, total = 0, None
        while total is None or start < total:
            body = {"request_data": {"filters": [], "search_from": start, "search_to": start + PAGE,
                                     "sort": {"field": "endpoint_id", "keyword": "asc"}}}
            r = await http.post(client, url, json=body, headers=_headers(s))
            reply = r.json()["reply"]
            total = int(reply.get("total_count") or 0)
            eps = reply.get("endpoints") or []
            if not eps:
                break
            for ep in eps:
                rec = to_record(ep)
                if rec:
                    records.append(rec)
            start += PAGE
    return dedupe_latest(records)
