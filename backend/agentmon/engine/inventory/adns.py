"""AD-integrated DNS'dagi `dnsRecord` atributini o'qish (MS-DNSP 2.3.2.2 DNS_RECORD)."""

from __future__ import annotations

import ipaddress
import struct
from datetime import datetime, timedelta, timezone

DNS_TYPE_A = 1
_EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


def parse_a_record(blob: bytes) -> tuple[str, datetime | None] | None:
    """A-yozuv bo'lsa (ip, vaqt) qaytaradi. Vaqt None — statik yozuv."""
    if len(blob) < 24:
        return None
    data_len, rtype = struct.unpack_from("<HH", blob, 0)
    if rtype != DNS_TYPE_A or data_len != 4 or len(blob) < 28:
        return None
    hours = struct.unpack_from("<I", blob, 20)[0]
    ip = str(ipaddress.IPv4Address(blob[24:28]))
    ts = _EPOCH_1601 + timedelta(hours=hours) if hours else None
    return ip, ts


def build_a_record(ip: str, ts: datetime | None) -> bytes:
    """Testlar uchun: DNS_RECORD A-yozuvini yasaydi."""
    hours = 0 if ts is None else int((ts - _EPOCH_1601).total_seconds() // 3600)
    return (
        struct.pack("<HHBBHI", 4, DNS_TYPE_A, 5, 0xF0, 0, 1)
        + struct.pack(">I", 1200)
        + struct.pack("<I", 0)
        + struct.pack("<I", hours)
        + ipaddress.IPv4Address(ip).packed
    )
