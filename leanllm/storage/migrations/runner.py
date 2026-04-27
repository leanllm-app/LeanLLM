"""Programmatic Alembic runner used by the CLI and PostgresEventStore.auto_migrate."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

POSTGRES_MIGRATIONS_DIR: Path = Path(__file__).parent / "postgres"


def _normalize_postgres_url(url: str) -> str:
    """Coerce a raw Postgres URL into the SQLAlchemy + asyncpg form."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


def _alembic_config(*, url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(POSTGRES_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _normalize_postgres_url(url))
    # env.py also reads this so subprocess invocations work
    os.environ["LEANLLM_ALEMBIC_URL"] = _normalize_postgres_url(url)
    return cfg


def upgrade_postgres(*, url: str, revision: str = "head") -> None:
    """Apply all pending migrations up to `revision` (default: head)."""
    from alembic import command

    cfg = _alembic_config(url=url)
    command.upgrade(cfg, revision)
    logger.info("[LeanLLM] Postgres migrations applied (target=%s).", revision)


def downgrade_postgres(*, url: str, revision: str = "-1") -> None:
    """Roll back to a previous revision (default: one step back)."""
    from alembic import command

    cfg = _alembic_config(url=url)
    command.downgrade(cfg, revision)
    logger.info("[LeanLLM] Postgres rolled back to %s.", revision)


def current_postgres(*, url: str) -> Optional[str]:
    """Return the current schema revision, or None if Alembic was never run."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _read() -> Optional[str]:
        engine = create_async_engine(_normalize_postgres_url(url), future=True)
        try:
            async with engine.connect() as conn:
                def _get(sync_conn):
                    ctx = MigrationContext.configure(sync_conn)
                    return ctx.get_current_revision()

                return await conn.run_sync(_get)
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def history_postgres(*, url: str) -> None:
    """Print the migration history (used by the CLI)."""
    from alembic import command

    cfg = _alembic_config(url=url)
    command.history(cfg, verbose=True)


async def auto_migrate_postgres(*, url: str) -> None:
    """
    Async-safe wrapper used by PostgresEventStore.initialize().

    Alembic itself runs synchronously and spins up its own event loop
    inside env.py, so we run it in a worker thread to avoid nesting loops.
    """
    await asyncio.to_thread(upgrade_postgres, url=url)
