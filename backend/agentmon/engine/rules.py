"""Holat qoidalari — sof funksiyalar, I/O yo'q. Butun biznes-logika shu yerda va to'liq test qilinadi."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agentmon.model import (
    CONFLICT,
    NO_SIGNAL,
    NOT_INSTALLED,
    OFFLINE,
    OK,
    PENDING,
    PROBLEM_STATES,
    STOPPED,
    UNHEALTHY,
)

# Konsol dalili
C_NONE = "none"          # mahsulotning konsoli yo'q (SearchInform)
C_UNKNOWN = "unknown"    # manba eskirgan yoki hali sinxronlanmagan — tayanib bo'lmaydi
C_MISSING = "missing"    # konsolda bu host yo'q
C_PRESENT = "present"


@dataclass(frozen=True, slots=True)
class ConsoleEvidence:
    kind: str
    healthy: bool = True
    reason: str | None = None
    last_seen: int | None = None      # agent konsolga oxirgi xabar bergan vaqt (unix)
    track_fresh: bool = True          # last_seen'ni yangilik uchun ishlatish mumkinmi (AD uchun yo'q)


@dataclass(frozen=True, slots=True)
class Evidence:
    now: int
    alive: bool
    alive_for: int           # joriy faollik seansi davomiyligi (soniya)
    net_last: int | None     # mahsulot serveriga oxirgi trafik
    thresh: int
    grace: int
    console: ConsoleEvidence
    net_denied: int | None = None   # FTD rad etgan oxirgi urinish (agent bor, lekin ACL bloklayapti)


def evaluate(e: Evidence) -> tuple[str, str | None]:
    if not e.alive:
        return OFFLINE, None

    c = e.console
    net_ok = e.net_last is not None and e.now - e.net_last <= e.thresh

    if net_ok:
        if c.kind in (C_NONE, C_UNKNOWN):
            return OK, None if c.kind == C_NONE else "Konsol ma'lumoti eskirgan, faqat tarmoq bo'yicha"
        if c.kind == C_MISSING:
            return CONFLICT, "Tarmoqda trafik bor, lekin konsolda bu host yo'q (boshqa serverga ulangan yoki nom mos emas)"
        if not c.healthy:
            return UNHEALTHY, c.reason
        return OK, None

    # Agent ulanmoqchi, lekin FTD rad etmoqda — bu o'rnatilmaganlik emas, tarmoq qoidasi muammosi.
    if e.net_denied is not None and e.now - e.net_denied <= e.thresh:
        return UNHEALTHY, "Agent serverga ulanishga urinmoqda, lekin FTD trafikni rad etmoqda (ACL tekshiring)"

    # Tarmoq jim. Hukm chiqarish uchun host yetarlicha uzoq tirik bo'lishi kerak.
    if e.alive_for < max(e.grace, e.thresh):
        return PENDING, None

    if c.kind in (C_NONE, C_UNKNOWN):
        return NO_SIGNAL, "Host tarmoqda faol, lekin agent serverga murojaat qilmayapti"
    if c.kind == C_MISSING:
        return NOT_INSTALLED, "Konsolda yo'q va serverga trafik yo'q"
    if c.track_fresh and c.last_seen is not None and e.now - c.last_seen <= e.thresh:
        return CONFLICT, ("Konsol agentni faol ko'rsatmoqda, lekin shu IP'dan serverga trafik yo'q "
                          "(agent boshqa yo'l bilan ulanmoqda yoki IP moslash xatosi)")
    return STOPPED, "Konsolda bor, lekin agent serverga murojaat qilmayapti (to'xtatilgan yoki o'chirilgan)"


@dataclass(slots=True)
class Tracked:
    """Host x mahsulot uchun joriy holat (engine xotirasida)."""

    state: str
    reason: str | None
    since: int
    last_known: str | None = None
    last_known_reason: str | None = None
    pending_state: str | None = None
    pending_count: int = 0


_TRANSIENT = frozenset({OFFLINE, PENDING})   # "oxirgi ma'lum holat"ni almashtirmaydi


def apply_debounce(cur: Tracked | None, new_state: str, new_reason: str | None, now: int, debounce: int) -> tuple[Tracked, bool]:
    """Yangi holatni qabul qiladi. Muammo holatiga o'tish `debounce` marta ketma-ket tasdiqlanishi kerak.

    Sof funksiya: `cur` o'zgartirilmaydi — natija DB'ga yozilgandan keyingina xotiraga olinadi.
    Qaytaradi: (yangi Tracked, DB'ga yozish kerakmi).
    """
    if cur is None:
        t = Tracked(new_state, new_reason, now)
        if new_state not in _TRANSIENT:
            t.last_known, t.last_known_reason = new_state, new_reason
        return t, True

    if new_state == cur.state:
        t = replace(cur, reason=new_reason, pending_state=None, pending_count=0)
        if new_state not in _TRANSIENT:
            t.last_known, t.last_known_reason = new_state, new_reason
        return t, new_reason != cur.reason

    if new_state in PROBLEM_STATES and debounce > 1:
        count = cur.pending_count + 1 if cur.pending_state == new_state else 1
        if count < debounce:
            return replace(cur, pending_state=new_state, pending_count=count), False

    t = Tracked(new_state, new_reason, now, cur.last_known, cur.last_known_reason)
    if new_state not in _TRANSIENT:
        t.last_known, t.last_known_reason = new_state, new_reason
    return t, True


def effective_state(t: Tracked) -> str:
    """Hisobotlar uchun: OFFLINE/PENDING bo'lsa oxirgi ma'lum holat."""
    if t.state in (OFFLINE, PENDING) and t.last_known:
        return t.last_known
    return t.state
