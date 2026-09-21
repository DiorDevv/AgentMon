"""Tarmoq signallari xotirada: har bir (ip, guruh) uchun oxirgi ko'rilgan vaqt.

Butun holat ~ (hostlar soni x 5) ta yozuv, shuning uchun hammasi xotirada turadi,
DB'ga esa faqat o'zgarganlari davriy ravishda yoziladi.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import orjson

from agentmon.engine.classify import Classifier

ANY = "any"


def denied_group(grp: str) -> str:
    """FTD rad etgan urinishlar alohida guruhda saqlanadi: 'cortex' -> 'cortex!'."""
    return grp + "!"


# NSEL NF_F_FW_EVENT
EV_CREATED, EV_TEARDOWN, EV_DENIED, EV_UPDATE = 1, 2, 3, 5
_SIGNAL_EVENTS = frozenset({0, EV_CREATED, EV_TEARDOWN, EV_UPDATE})
_PRESENCE_EVENTS = _SIGNAL_EVENTS | {EV_DENIED}


@dataclass(slots=True)
class Presence:
    site: str
    first_seen: int
    alive_since: int   # joriy uzluksiz faollik seansi boshlangan vaqt
    last_seen: int


@dataclass
class IngestStats:
    received: int = 0
    accepted: int = 0
    malformed: int = 0
    by_event: Counter = field(default_factory=Counter)
    last_rx_wall: float | None = None   # oxirgi hodisa kelgan real vaqt
    watermark: int | None = None        # qayta ishlangan eng katta hodisa vaqti
    last_update_event: int | None = None


class SignalStore:
    def __init__(self, alive_gap: int) -> None:
        self.alive_gap = alive_gap
        self.presence: dict[str, Presence] = {}
        self.last: dict[tuple[str, str], int] = {}
        self.dirty_presence: set[str] = set()
        self.dirty_signal: set[tuple[str, str]] = set()
        self.stats = IngestStats()

    # --- yuklash (engine qayta ishga tushganda) ---
    def load(self, presence: dict[str, Presence], last: dict[tuple[str, str], int]) -> None:
        self.presence = presence
        self.last = last

    # --- asosiy yo'l ---
    def observe(self, ip: str, site: str, grp: str | None, ts: int) -> None:
        p = self.presence.get(ip)
        if p is None:
            self.presence[ip] = Presence(site, ts, ts, ts)
        else:
            if ts > p.last_seen:
                if ts - p.last_seen > self.alive_gap:
                    p.alive_since = ts   # uzilishdan keyin yangi seans
                p.last_seen = ts
            p.site = site
        self.dirty_presence.add(ip)
        if grp:
            key = (ip, grp)
            if ts > self.last.get(key, 0):
                self.last[key] = ts
                self.dirty_signal.add(key)

    def process_raw(self, raw: bytes | str, clf: Classifier, now: int) -> None:
        st = self.stats
        st.received += 1
        try:
            ev = orjson.loads(raw)
            src = ev["src"]
            dst = ev["dst"]
            dport = int(ev.get("dport") or 0)
            code = int(ev.get("ev") or 0)
            ts = int(ev["ts"])
        except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
            st.malformed += 1
            return
        st.by_event[code] += 1
        if code == EV_UPDATE:
            st.last_update_event = ts
        if code not in _PRESENCE_EVENTS:
            return
        site = clf.site_of(src)
        if site is None:
            return
        ts = min(ts, now)
        grp = clf.target_group(dst, dport)
        if code == EV_DENIED:
            # Rad etilgan urinish host tirikligini isbotlaydi, lekin agent ishlashini emas.
            self.observe(src, site, denied_group(grp) if grp else None, ts)
        else:
            self.observe(src, site, grp, ts)
        st.accepted += 1
        if st.watermark is None or ts > st.watermark:
            st.watermark = ts

    # --- so'rovlar ---
    def last_seen(self, ip: str, grp: str) -> int | None:
        if grp == ANY:
            p = self.presence.get(ip)
            return p.last_seen if p else None
        return self.last.get((ip, grp))

    def take_dirty(self) -> tuple[list[tuple[str, Presence]], list[tuple[str, str, int]]]:
        pres = [(ip, self.presence[ip]) for ip in self.dirty_presence if ip in self.presence]
        sig = [(ip, g, self.last[(ip, g)]) for ip, g in self.dirty_signal if (ip, g) in self.last]
        self.dirty_presence = set()
        self.dirty_signal = set()
        return pres, sig

    def prune(self, before: int) -> int:
        """`before` dan eski yozuvlarni xotiradan o'chiradi (DHCP almashinuvi bilan to'planadigan IP'lar)."""
        old_ips = [ip for ip, p in self.presence.items() if p.last_seen < before]
        for ip in old_ips:
            del self.presence[ip]
            self.dirty_presence.discard(ip)
        old_keys = [k for k, t in self.last.items() if t < before]
        for k in old_keys:
            del self.last[k]
            self.dirty_signal.discard(k)
        return len(old_ips)

    def restore_dirty(self, pres: list[tuple[str, Presence]], sig: list[tuple[str, str, int]]) -> None:
        """DB'ga yozish muvaffaqiyatsiz bo'lsa — keyingi safar qayta urinish uchun."""
        self.dirty_presence.update(ip for ip, _ in pres)
        self.dirty_signal.update((ip, g) for ip, g, _ in sig)
