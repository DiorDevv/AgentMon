"""Jonli demo trafik: demo tarmoqni haqiqiy NSEL (NetFlow v9) paketlari bilan Logstash'ga "o'ynaydi".

Butun zanjir haqiqiy ishlaydi: Logstash -> Redis -> Engine -> PostgreSQL -> Web.
Vaqti-vaqti bilan tasodifiy hostda agent "to'xtatiladi" — engine uni qanday aniqlashini kuzatish uchun.

FAQAT demo muhitida (docker-compose.demo.yml orqali) ishga tushiriladi:
    docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
import socket
import struct
import time

import asyncpg

from agentmon.config import get_settings, parse_endpoints
from agentmon.demo import DC_IP, build_plan, signal_groups, unknown_devices

log = logging.getLogger("agentmon.demo_live")

CYCLE = 60                 # har bir host daqiqada bir marta "heartbeat" yuboradi
RECORDS_PER_PACKET = 30
INCIDENT_EVERY = 600       # har 10 daqiqada bitta yangi "muammo"
INCIDENT_DURATION = 5400   # 90 daqiqa davom etadi, keyin agent "tiklanadi"
INTERNET = ("93.184.216.34", 443)

TEMPLATE_ID = 256
FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (148, 4), (233, 1), (323, 8)]
_TEMPLATE = struct.pack("!HH", TEMPLATE_ID, len(FIELDS)) + b"".join(struct.pack("!HH", t, n) for t, n in FIELDS)


def _flowset(fs_id: int, body: bytes) -> bytes:
    pad = (-(len(body) + 4)) % 4
    return struct.pack("!HH", fs_id, len(body) + 4 + pad) + body + b"\x00" * pad


def nsel_packet(seq: int, records: list[tuple[str, str, int, int]]) -> bytes:
    """Cisco ASA/FTD NSEL formatidagi NetFlow v9 paketi (template + data)."""
    now_ms = int(time.time() * 1000)
    data = b"".join(
        ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
        + struct.pack("!HHBIBQ", 50000 + i, dport, 6, (seq * 100 + i) & 0xFFFFFFFF, ev, now_ms)
        for i, (src, dst, dport, ev) in enumerate(records))
    body = _flowset(0, _TEMPLATE) + _flowset(TEMPLATE_ID, data)
    header = struct.pack("!HHIIII", 9, 1 + len(records), int(time.monotonic() * 1000) & 0xFFFFFFFF,
                         int(time.time()), seq & 0xFFFFFFFF, 0)
    return header + body


def guard() -> None:
    s = get_settings()
    if os.environ.get("DEMO_LIVE") != "yes":
        raise SystemExit("demo_live faqat DEMO_LIVE=yes bilan ishlaydi (docker-compose.demo.yml)")
    if s.ad_server or s.cortex_fqdn or s.ksc_url:
        raise SystemExit("Real manbalar sozlangan — demo trafik real muhitga aralashmasligi uchun ishga tushirilmaydi")


async def keep_sources_fresh(dsn: str) -> None:
    """Demo'da API manbalari yo'q — konsol ma'lumotlarini "yangi" deb belgilab turamiz."""
    while True:
        try:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute("UPDATE source_status SET last_success = now(), last_attempt = now() "
                                   "WHERE source IN ('ad', 'cortex', 'ksc')")
            finally:
                await conn.close()
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("source_status yangilanmadi: %s", e)
        await asyncio.sleep(600)


async def traffic(collector: tuple[str, int], n_hosts: int) -> None:
    s = get_settings()
    targets = {
        "ad": (DC_IP, 88),
        "cortex": parse_endpoints(s.target_cortex)[0],
        "ksc": parse_endpoints(s.target_ksc)[0],
        "si": parse_endpoints(s.target_si)[0],
    }
    plan = [(name, ip, signal_groups(f)) for name, ip, _site, f in build_plan(n_hosts) if not f["offline"]]
    unknown = [(ip, ["ad"] if has_dc else []) for ip, _site, has_dc in unknown_devices()]
    healthy = [(name, ip, g) for name, ip, g in plan if "cortex" in g and "ksc" in g]
    muted: dict[tuple[str, str], float] = {}     # (ip, guruh) -> qachongacha jim
    rnd = random.Random()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq, next_incident = 0, time.time() + 120

    while True:
        t0 = time.time()
        if t0 >= next_incident and healthy:
            name, ip, _ = rnd.choice(healthy)
            grp = rnd.choice(["cortex", "ksc", "si"])
            muted[(ip, grp)] = t0 + INCIDENT_DURATION
            log.warning("DEMO HODISA: %s (%s) da %s agenti 90 daqiqaga to'xtatildi — engine ~%d daqiqada aniqlaydi",
                        name.upper(), ip, grp.upper(), s.thresholds[grp] // 60 + 2)
            next_incident = t0 + INCIDENT_EVERY
        for key in [k for k, until in muted.items() if until <= t0]:
            log.info("DEMO: %s da %s agenti tiklandi", key[0], key[1].upper())
            del muted[key]

        records: list[tuple[str, str, int, int]] = []
        for ip, groups in [(ip, g) for _n, ip, g in plan] + unknown:
            records.append((ip, *INTERNET, 1))
            records.extend((ip, *targets[g], 5) for g in groups if (ip, g) not in muted)
        rnd.shuffle(records)
        for i in range(0, len(records), RECORDS_PER_PACKET):
            seq += 1
            sock.sendto(nsel_packet(seq, records[i:i + RECORDS_PER_PACKET]), collector)
            if seq % 50 == 0:
                await asyncio.sleep(0.02)   # UDP buferini to'ldirib yubormaslik uchun
        log.info("yuborildi: %d yozuv, %d paket, jim agentlar: %d", len(records),
                 -(-len(records) // RECORDS_PER_PACKET), len(muted))
        await asyncio.sleep(max(1.0, CYCLE - (time.time() - t0)))


async def main() -> None:
    guard()
    s = get_settings()
    host = os.environ.get("DEMO_COLLECTOR", "logstash")
    collector = (socket.gethostbyname(host), int(os.environ.get("DEMO_COLLECTOR_PORT", "2055")))
    n = int(os.environ.get("DEMO_HOSTS", "4000"))
    log.info("Jonli demo trafik: %d host -> %s:%d", n, *collector)
    await asyncio.gather(traffic(collector, n), keep_sources_fresh(s.database_url))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
