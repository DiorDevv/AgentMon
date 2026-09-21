"""IP -> host moslash (identity resolution).

DHCP/DC loglari yo'q, shuning uchun bir nechta mustaqil dalil birlashtiriladi:
agentlarning o'zi xabar bergan IP (Cortex, KSC) va AD-integrated DNS yozuvlari.

Asosiy xavf — DHCP IP'ni boshqa qurilmaga bergan, lekin eski dalil (DNS yozuvi) hali
eski egasini ko'rsatib turibdi. Shunda eski egasi "tirik, lekin agent jim" bo'lib
ko'rinadi, haqiqiy (balki begona) qurilma esa yashirinadi. Bunga qarshi uchta qoida:

1. Bitta IP bir nechta nomga ko'rsatsa — eng yangi dalil g'olib.
2. Agent hostning hozirgi IP'larini yangi xabar qilgan bo'lsa, o'sha host haqidagi
   eskiroq dalillar (boshqa IP'lardagi eski DNS yozuvlari) bekor qilinadi.
3. Faqat eski DNS dalili bilan moslangan IP domen a'zosidek ishlashi kerak (DC bilan
   trafik). Aks holda moslash ishonchsiz — IP "noma'lum qurilma" sifatida ko'rsatiladi.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Vaqt teng bo'lsa: agent xabari DNS'dan ishonchliroq.
SOURCE_PRIORITY = {"cortex": 3, "ksc": 3, "dns": 1}
AGENT_SOURCES = frozenset({"cortex", "ksc"})
# Agent dalili shu qadar yangi bo'lsa, o'sha hostning eskiroq boshqa-IP dalillari bekor qilinadi.
SUPERSEDE_MARGIN = 86400


@dataclass(frozen=True, slots=True)
class Candidate:
    ip: str
    name: str
    source: str
    observed_at: int | None   # None = statik DNS yozuvi


@dataclass(frozen=True, slots=True)
class Resolved:
    name: str
    source: str
    observed_at: int | None
    alternatives: int          # boshqa nomlar soni (ziddiyat ko'rsatkichi)


def resolve(candidates: list[Candidate], now: int, max_age: int) -> dict[str, Resolved]:
    fresh = [c for c in candidates if c.observed_at is None or now - c.observed_at <= max_age]

    # 2-qoida: agent xabar bergan eng yangi vaqt (host bo'yicha).
    agent_newest: dict[str, int] = {}
    for c in fresh:
        if c.source in AGENT_SOURCES and c.observed_at is not None:
            if c.observed_at > agent_newest.get(c.name, 0):
                agent_newest[c.name] = c.observed_at
    agent_ips: dict[str, set[str]] = {}
    for c in fresh:
        if c.source in AGENT_SOURCES and c.observed_at is not None \
                and c.observed_at >= agent_newest[c.name] - SUPERSEDE_MARGIN:
            agent_ips.setdefault(c.name, set()).add(c.ip)

    by_ip: dict[str, list[Candidate]] = {}
    for c in fresh:
        newest = agent_newest.get(c.name)
        if newest is not None and c.ip not in agent_ips[c.name] \
                and (c.observed_at or 0) < newest - SUPERSEDE_MARGIN:
            continue   # host allaqachon boshqa IP'da ekani ma'lum
        by_ip.setdefault(c.ip, []).append(c)

    out: dict[str, Resolved] = {}
    for ip, cands in by_ip.items():
        best = max(cands, key=lambda c: (c.observed_at or 0, SOURCE_PRIORITY.get(c.source, 0)))
        others = {c.name for c in cands} - {best.name}
        out[ip] = Resolved(best.name, best.source, best.observed_at, len(others))
    return out


def verify(resolved: dict[str, Resolved], now: int, trust_age: int,
           has_dc_traffic: Callable[[str], bool]) -> dict[str, Resolved]:
    """3-qoida: eski/statik DNS dalili faqat IP domen trafigini ko'rsatsa qabul qilinadi."""
    out = {}
    for ip, r in resolved.items():
        if r.source == "dns" and (r.observed_at is None or now - r.observed_at > trust_age) \
                and not has_dc_traffic(ip):
            continue
        out[ip] = r
    return out
