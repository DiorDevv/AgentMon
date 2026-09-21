"""API chaqiruvlari uchun qayta urinish: vaqtinchalik xatolar (429, 5xx, tarmoq) butun sinxronizatsiyani buzmasin."""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


async def post(client: httpx.AsyncClient, url: str, *, attempts: int = 4, base_delay: float = 2.0,
               **kw) -> httpx.Response:
    for attempt in range(1, attempts + 1):
        try:
            r = await client.post(url, **kw)
        except httpx.TransportError as e:
            if attempt == attempts:
                raise
            log.warning("%s: tarmoq xatosi (%s), qayta urinish %d/%d", url, e, attempt, attempts - 1)
        else:
            if r.status_code not in RETRY_STATUS or attempt == attempts:
                r.raise_for_status()
                return r
            log.warning("%s: HTTP %d, qayta urinish %d/%d", url, r.status_code, attempt, attempts - 1)
        await asyncio.sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")
