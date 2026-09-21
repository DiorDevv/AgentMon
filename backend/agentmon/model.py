from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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
