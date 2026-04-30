"""CLI shared helpers — open a query-capable store from env / args.

CLI is a one-shot process, so it does NOT use the SDK's worker (which lives in a
daemon thread bound to its own loop). Instead the CLI opens its own store on the
caller's event loop (driven by `asyncio.run(...)` in the dispatcher) and closes
it before returning.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import LeanLLMConfig
from ..storage import create_store
from ..storage.base import BaseEventStore
from ..storage.remote import RemoteEventStore


_REMOTE_NOT_SUPPORTED = (
    "Error: CLI query commands need a local backend (Postgres or SQLite).\n"
    "Set LEANLLM_DATABASE_URL — the SaaS read API is on the roadmap but not\n"
    "yet available, so LEANLLM_API_KEY alone won't work for `logs` / `show` /\n"
    "`replay`.\n"
)


async def open_store(*, url_arg: Optional[str] = None) -> BaseEventStore:
    """Build and initialize a store from `--url` or env. Refuses Remote."""
    cfg = LeanLLMConfig.from_env()
    database_url = url_arg or cfg.database_url

    if not database_url:
        if cfg.leanllm_api_key:
            sys.stderr.write(_REMOTE_NOT_SUPPORTED)
            sys.exit(2)
        sys.stderr.write(
            "Error: no database URL provided.\n"
            "Pass --url or set LEANLLM_DATABASE_URL.\n"
        )
        sys.exit(2)

    store = create_store(database_url=database_url, auto_migrate=False)
    if isinstance(
        store, RemoteEventStore
    ):  # defensive; create_store shouldn't reach here
        sys.stderr.write(_REMOTE_NOT_SUPPORTED)
        sys.exit(2)

    await store.initialize()
    return store


def parse_when(value: str) -> datetime:
    """Parse a CLI time argument.

    Accepts:
      - ISO-8601: "2026-04-27T10:00:00", "2026-04-27"
      - relative-ago: "1h", "30m", "2d" (interpreted as "now - that duration")
    Returns a timezone-aware UTC datetime.
    """
    if not value:
        raise ValueError("empty time value")
    suffix = value[-1].lower()
    if suffix in ("h", "m", "d") and value[:-1].isdigit():
        amount = int(value[:-1])
        if suffix == "h":
            delta = timedelta(hours=amount)
        elif suffix == "m":
            delta = timedelta(minutes=amount)
        else:
            delta = timedelta(days=amount)
        return datetime.now(timezone.utc) - delta
    # ISO path. Accept naive and treat as UTC.
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
