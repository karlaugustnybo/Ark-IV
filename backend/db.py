from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import asyncpg
from litestar import Litestar
from litestar.datastructures import State

from backend.config import ROOT, get_settings

Pool = asyncpg.Pool


@asynccontextmanager
async def database_lifespan(app: Litestar) -> AsyncGenerator[None]:
    from backend.coverage_profiles import load_profiles

    registry = load_profiles(get_settings())
    pools = {}
    try:
        for key, settings in registry.items():
            pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=settings.db_pool_min_size,
                max_size=settings.db_pool_max_size,
                command_timeout=30,
            )
            pools[key] = pool
            await apply_schema(pool, ROOT / "backend/schema.sql")
        app.state.coverage_settings = registry
        app.state.coverage_pools = pools
        app.state.db_pool = pools["resident"]
        yield
    finally:
        for pool in pools.values():
            await pool.close()


def get_pool(state: State) -> Pool:
    from backend.coverage_profiles import current_profile

    registry = getattr(state, "coverage_pools", None)
    return cast(Pool, registry[current_profile.get()] if registry is not None else state.db_pool)


async def apply_schema(pool: Pool, schema_path: Path) -> None:
    async with pool.acquire() as connection:
        await connection.execute(schema_path.read_text())
