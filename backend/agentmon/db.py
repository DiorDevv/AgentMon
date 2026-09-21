from __future__ import annotations

from importlib import resources

import asyncpg
import orjson


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ, encoder=lambda v: orjson.dumps(v).decode(), decoder=orjson.loads, schema="pg_catalog"
        )


async def create_pool(dsn: str, **kw) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, init=_init_conn, **kw)


async def migrate(pool: asyncpg.Pool) -> None:
    sql = resources.files("agentmon").joinpath("schema.sql").read_text()
    async with pool.acquire() as conn:
        # Bir nechta jarayon bir vaqtda ishga tushsa ham xavfsiz.
        await conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            await conn.execute(sql)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(424242)")


async def set_status(pool: asyncpg.Pool, key: str, value: dict) -> None:
    await pool.execute(
        """INSERT INTO system_status (key, value, updated_at) VALUES ($1, $2, now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        key,
        value,
    )
