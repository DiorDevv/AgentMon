"""Manbalardan kanonik hostlar ro'yxatini yig'ish (sof funksiya)."""

from __future__ import annotations

from dataclasses import dataclass

from agentmon.model import ConsoleRecord, is_server_os, norm_host

SOURCE_ORDER = ("ad", "cortex", "ksc")


@dataclass(slots=True)
class HostInfo:
    name: str
    display_name: str
    fqdn: str | None
    os: str | None
    sources: list[str]


def _is_server(r: ConsoleRecord) -> bool:
    return is_server_os(r.os) or r.details.get("endpoint_type") == "AGENT_TYPE_SERVER"


def build_hosts(consoles: dict[str, dict[str, ConsoleRecord]], include_servers: bool) -> dict[str, HostInfo]:
    """AD'dagi faol hisoblar + agent konsollaridagi barcha hostlar birlashmasi.

    AD'da o'chirilgan yoki uzoq vaqt kirmagan (stale) hisob o'zi yolg'iz host yaratmaydi —
    aks holda ro'yxat eski kompyuterlar bilan to'lib ketadi.
    """
    names: set[str] = set()
    for product in SOURCE_ORDER:
        for name, r in consoles.get(product, {}).items():
            if product == "ad" and (r.details.get("stale") or not r.details.get("enabled", True)):
                continue
            names.add(name)

    out: dict[str, HostInfo] = {}
    for name in names:
        recs = [consoles[p][name] for p in SOURCE_ORDER if name in consoles.get(p, {})]
        if not include_servers and any(_is_server(r) for r in recs):
            continue
        out[name] = HostInfo(
            name=name,
            # Eng to'liq nom: AD/KSC NetBIOS (15 belgi) nomini, Cortex esa to'liq hostname'ni beradi.
            display_name=max((norm_host(r.display_name) or "" for r in recs), key=len, default="").upper()
            or name.upper(),
            fqdn=next((r.fqdn for r in recs if r.fqdn), None),
            os=next((r.os for r in recs if r.os), None),
            sources=[r.product for r in recs],
        )
    return out
