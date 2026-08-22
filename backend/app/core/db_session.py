"""
app/core/db_session.py

Every request sets `app.current_org` as a Postgres session variable before
any query runs. This activates the RLS policies defined in the migration —
a second, database-layer guarantee that one org can never read another's
data, even if an application-layer filter is accidentally omitted somewhere.

Usage in API route handlers:
    async for session in get_tenant_session(current_user.org_id):
        result = await session.execute(...)

Or use the FastAPI dependency `get_db` in api/deps.py, which calls this
function and yields the scoped session automatically.
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_size=20 / max_overflow=10 matches the plan's recommendation.
# echo=False in production; flip to True during local debugging if needed.
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    echo=False,
)

# ── Session factory ───────────────────────────────────────────────────────────
# expire_on_commit=False: keeps ORM objects usable after commit without
# issuing an implicit re-SELECT (important in async context).
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# ── Declarative base (shared by all ORM models) ───────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Tenant-scoped session ─────────────────────────────────────────────────────

async def get_tenant_session(org_id: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession with the Postgres RLS tenant variable set.

    `set_config('app.current_org', <value>, true)` binds `app.current_org`
    for the duration of this transaction only (the third argument `true` makes
    it transaction-local, equivalent to SET LOCAL). Unlike `SET LOCAL`, the
    `set_config()` function accepts bind parameters and works correctly with
    asyncpg.
    This activates the RLS policies:

        CREATE POLICY tenant_isolation_meetings ON meetings
            USING (org_id = current_setting('app.current_org')::uuid);

    Even if an application-layer WHERE clause is accidentally omitted on a
    future query, the database will still refuse cross-tenant reads/writes.

    Args:
        org_id: The authenticated user's organisation UUID (string form).

    Yields:
        An AsyncSession ready for use. The caller must NOT call session.close();
        the async context manager handles cleanup.
    """
    async with SessionLocal() as session:
        # Set the RLS context variable for this transaction
        await session.execute(
            text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
