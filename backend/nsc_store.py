"""Persistence helpers for NSC Novix Simulation Core.

The current Render POC still runs with in-memory dictionaries in the legacy
engine. These helpers are the first Plan B step toward PostgreSQL persistence.
They are intentionally small and safe: if DATABASE_URL is not configured, the
application keeps running in POC mode.
"""

import os
from typing import Any, Dict, Optional

import asyncpg

_POOL: Optional[asyncpg.Pool] = None


def database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL") or os.getenv("EXTERNAL_DB_URL"))


async def get_pool() -> Optional[asyncpg.Pool]:
    global _POOL
    if _POOL:
        return _POOL
    database_url = os.getenv("DATABASE_URL") or os.getenv("EXTERNAL_DB_URL")
    if not database_url:
        return None
    _POOL = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
    return _POOL


async def persist_audit_event(event_type: str, payload: Dict[str, Any], tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> bool:
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into nsc_audit.events(tenant_id, user_id, event_type, payload)
            values($1::uuid, $2::uuid, $3, $4::jsonb)
            """,
            tenant_id,
            user_id,
            event_type,
            payload,
        )
    return True


async def persistence_status() -> Dict[str, Any]:
    pool = await get_pool()
    if not pool:
        return {"configured": False, "mode": "memory_poc"}
    async with pool.acquire() as conn:
        value = await conn.fetchval("select now()::text")
    return {"configured": True, "mode": "postgres", "server_time": value}
