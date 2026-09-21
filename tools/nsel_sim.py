#!/usr/bin/env python3
"""NSEL (NetFlow v9, Cisco ASA/FTD formatida) simulyatori — faqat standart kutubxona.

FTD'ni ulashdan oldin Logstash -> Redis -> Engine zanjirini tekshirish uchun:

    python3 tools/nsel_sim.py --collector 127.0.0.1 --src 10.10.1.50 --target 172.25.44.50:8888
    python3 tools/nsel_sim.py --collector 127.0.0.1 --src 10.10.1.50 --target 172.25.25.111:13000 --event 5

Keyin: docker compose exec redis redis-cli LLEN nsel  (engine to'xtatilgan bo'lsa ko'rinadi)
yoki web -> Tizim holati -> "Jami qabul qilingan".
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import time

TEMPLATE_ID = 256
# (IANA/Cisco field id, uzunlik)
FIELDS = [
    (8, 4),     # IPV4_SRC_ADDR
    (12, 4),    # IPV4_DST_ADDR
    (7, 2),     # L4_SRC_PORT
    (11, 2),    # L4_DST_PORT
    (4, 1),     # PROTOCOL
    (148, 4),   # NF_F_CONN_ID
    (233, 1),   # NF_F_FW_EVENT
    (323, 8),   # NF_F_EVENT_TIME_MSEC
]


def _flowset(fs_id: int, body: bytes) -> bytes:
    pad = (-(len(body) + 4)) % 4
    return struct.pack("!HH", fs_id, len(body) + 4 + pad) + body + b"\x00" * pad


def packet(seq: int, records: list[tuple[str, str, int, int, int]]) -> bytes:
    tmpl = struct.pack("!HH", TEMPLATE_ID, len(FIELDS)) + b"".join(struct.pack("!HH", t, n) for t, n in FIELDS)
    now_ms = int(time.time() * 1000)
    data = b""
    for i, (src, dst, sport, dport, ev) in enumerate(records):
        data += (ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
                 + struct.pack("!HHBIBQ", sport, dport, 6, seq * 1000 + i, ev, now_ms))
    body = _flowset(0, tmpl) + _flowset(TEMPLATE_ID, data)
    header = struct.pack("!HHIIII", 9, 1 + len(records), int(time.monotonic() * 1000) & 0xFFFFFFFF,
                         int(time.time()), seq, 0)
    return header + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collector", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2055)
    ap.add_argument("--src", action="append", required=True, help="foydalanuvchi IP (bir necha marta berish mumkin)")
    ap.add_argument("--target", action="append", required=True, help="ip:port (bir necha marta berish mumkin)")
    ap.add_argument("--event", type=int, default=1, help="1=created 2=teardown 3=denied 5=update")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    records = []
    for src in a.src:
        for t in a.target:
            ip, _, port = t.rpartition(":")
            records.append((src, ip, 50000, int(port), a.event))
    for n in range(a.repeat):
        sock.sendto(packet(n + 1, records), (a.collector, a.port))
        print(f"yuborildi: {len(records)} yozuv -> {a.collector}:{a.port}")
        if n + 1 < a.repeat:
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
