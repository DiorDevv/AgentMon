from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

log = logging.getLogger(__name__)

# Holatlar
OK = "OK"
UNHEALTHY = "UNHEALTHY"          # agent ishlaydi, lekin himoya to'liq emas
STOPPED = "STOPPED"              # konsolda bor, lekin tarmoqda jim
NOT_INSTALLED = "NOT_INSTALLED"  # konsolda yo'q va tarmoqda jim
NO_SIGNAL = "NO_SIGNAL"          # tarmoqda jim, konsol ma'lumoti yo'q (SI yoki manba eskirgan)
CONFLICT = "CONFLICT"            # tarmoq va konsol bir-biriga zid
PENDING = "PENDING"              # host yaqinda yoqilgan, hukm chiqarishga erta
OFFLINE = "OFFLINE"              # host tarmoqda ko'rinmayapti

PROBLEM_STATES = frozenset({UNHEALTHY, STOPPED, NOT_INSTALLED, NO_SIGNAL, CONFLICT})
# Tarmoq jimligidan kelib chiqadigan holatlar (ommaviy uzilishda ushlab turiladi).
SILENCE_STATES = frozenset({STOPPED, NOT_INSTALLED, NO_SIGNAL})


def norm_host(name: str | None) -> str | None:
    """'CORP\\PC-0412$', 'pc-0412.corp.local' -> 'pc-0412'"""
    if not name:
        return None
    n = name.strip().lower()
    if "\\" in n:
        n = n.rsplit("\\", 1)[1]
    n = n.split(".", 1)[0].rstrip("$")
    return n or None


# Windows kompyuterni domenda NetBIOS nomi (hostname'ning birinchi 15 belgisi = sAMAccountName) bo'yicha
# noyob qiladi. AD va KSC shu qisqa nomni, Cortex va DNS esa to'liq hostname'ni beradi — shuning uchun
# manbalarni birlashtirish kaliti ham shu 15 belgi.
NETBIOS_LEN = 15


def netbios_key(name: str | None) -> str | None:
    """'ACCOUNTING-LAPTOP-01.corp.local' -> 'accounting-lapt'"""
    n = norm_host(name)
    return n[:NETBIOS_LEN] if n else None


def is_server_os(os_name: str | None) -> bool:
    return bool(os_name) and "server" in os_name.lower()


@dataclass(slots=True)
class ConsoleRecord:
    """Bitta manbaning (AD, Cortex, KSC) bitta host haqidagi normallashtirilgan ko'rinishi."""

    product: str
    name: str                       # norm_host() natijasi
    display_name: str
    healthy: bool
    reason: str | None = None
    fqdn: str | None = None
    os: str | None = None
    ips: list[str] = field(default_factory=list)
    last_seen: datetime | None = None   # agent o'zi oxirgi marta xabar bergan vaqt
    version: str | None = None
    details: dict = field(default_factory=dict)


def dedupe_latest(records: list[ConsoleRecord]) -> list[ConsoleRecord]:
    """Bir nom bir necha marta uchrasa (agent qayta o'rnatilgan), eng yangisini qoldiradi."""
    best: dict[str, ConsoleRecord] = {}
    floor = datetime.min
    for r in records:
        cur = best.get(r.name)
        if cur is None:
            best[r.name] = r
            continue
        a = (cur.last_seen or floor).replace(tzinfo=None)
        b = (r.last_seen or floor).replace(tzinfo=None)
        if b > a:
            best[r.name] = r
    return list(best.values())


def assign_keys(records: list[ConsoleRecord]) -> list[ConsoleRecord]:
    """Bitta manba yozuvlariga birlashtirish kalitini (NetBIOS nomi) beradi.

    `name` maydonida to'liq normallashtirilgan nom kutiladi. Bir kalitga ikki xil uzun nom tushsa
    (domensiz qurilmalar: Linux, macOS — Windows domenida bu mumkin emas), ular jimgina qo'shilib
    ketmasligi uchun to'liq nomlari bilan qoldiriladi.
    """
    groups: dict[str, list[ConsoleRecord]] = {}
    for r in dedupe_latest(records):
        groups.setdefault(r.name[:NETBIOS_LEN], []).append(r)
    out: list[ConsoleRecord] = []
    for key, rs in groups.items():
        long_names = {r.name for r in rs if len(r.name) > NETBIOS_LEN}
        if len(long_names) <= 1:
            # Bitta kompyuter (qisqa va to'liq nom bilan ikki marta uchragan bo'lishi mumkin) — eng yangisi.
            best = dedupe_latest([replace(r, name=key) for r in rs])[0]
            out.append(best)
        else:
            log.warning("NetBIOS nomi to'qnashuvi %r: %s — to'liq nomlar bilan qoldirildi", key, sorted(long_names))
            out.extend(rs)
    return out
