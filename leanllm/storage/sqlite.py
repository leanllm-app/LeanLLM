from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, List
from urllib.parse import urlparse

from .base import BaseEventStore

if TYPE_CHECKING:
    from ..events.models import LLMEvent

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS llm_events (
    event_id                TEXT PRIMARY KEY,
    timestamp               TEXT NOT NULL,
    model                   TEXT NOT NULL,
    provider                TEXT NOT NULL,
    input_tokens            INTEGER NOT NULL DEFAULT 0,
    output_tokens           INTEGER NOT NULL DEFAULT 0,
    total_tokens            INTEGER NOT NULL DEFAULT 0,
    cost                    REAL NOT NULL DEFAULT 0,
    latency_ms              INTEGER NOT NULL DEFAULT 0,
    labels                  TEXT NOT NULL DEFAULT '{}',
    prompt                  TEXT,
    response                TEXT,
    metadata                TEXT NOT NULL DEFAULT '{}',
    schema_version          INTEGER NOT NULL DEFAULT 1,
    correlation_id          TEXT,
    parent_request_id       TEXT,
    parameters              TEXT NOT NULL DEFAULT '{}',
    tools                   TEXT,
    tool_calls              TEXT,
    time_to_first_token_ms  INTEGER,
    total_stream_time_ms    INTEGER,
    error_kind              TEXT,
    error_message           TEXT,
    normalized_input        TEXT,
    normalized_output       TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_llm_events_timestamp ON llm_events (timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_llm_events_model     ON llm_events (model);",
    "CREATE INDEX IF NOT EXISTS idx_llm_events_correlation_id ON llm_events (correlation_id);",
    "CREATE INDEX IF NOT EXISTS idx_llm_events_parent_request_id ON llm_events (parent_request_id);",
    "CREATE INDEX IF NOT EXISTS idx_llm_events_error_kind ON llm_events (error_kind);",
]

_INSERT = """
INSERT OR IGNORE INTO llm_events (
    event_id, timestamp, model, provider,
    input_tokens, output_tokens, total_tokens,
    cost, latency_ms, labels, prompt, response, metadata, schema_version,
    correlation_id, parent_request_id, parameters, tools, tool_calls,
    time_to_first_token_ms, total_stream_time_ms, error_kind, error_message,
    normalized_input, normalized_output
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _path_from_url(url: str) -> str:
    """
    Parse a SQLAlchemy-style sqlite URL.

    sqlite://                   → :memory:
    sqlite:///:memory:          → :memory:
    sqlite:///./events.db       → ./events.db   (relative)
    sqlite:////tmp/events.db    → /tmp/events.db (absolute)
    """
    if url in ("sqlite://", "sqlite:///", "sqlite:///:memory:"):
        return ":memory:"
    raw = urlparse(url).path
    if raw.startswith("//"):
        return raw[1:]   # absolute → keep one leading slash
    if raw.startswith("/"):
        return raw[1:]   # relative → strip the leading slash
    return raw or ":memory:"


def _to_row(event: "LLMEvent") -> tuple:
    return (
        event.event_id,
        event.timestamp.isoformat(),
        event.model,
        event.provider,
        event.input_tokens,
        event.output_tokens,
        event.total_tokens,
        event.cost,
        event.latency_ms,
        json.dumps(event.labels),
        event.prompt,
        event.response,
        json.dumps(event.metadata),
        event.schema_version,
        event.correlation_id,
        event.parent_request_id,
        json.dumps(event.parameters) if event.parameters else json.dumps({}),
        json.dumps(event.tools) if event.tools else None,
        json.dumps(event.tool_calls) if event.tool_calls else None,
        event.time_to_first_token_ms,
        event.total_stream_time_ms,
        event.error_kind.value if event.error_kind else None,
        event.error_message,
        json.dumps(event.normalized_input.model_dump(mode="json")) if event.normalized_input else None,
        json.dumps(event.normalized_output.model_dump(mode="json")) if event.normalized_output else None,
    )


class SQLiteEventStore(BaseEventStore):
    """
    aiosqlite-backed event store. Zero-config — great for dev and tests.

    Note: SQLite uses TEXT for JSON columns (no native JSONB).
    Use Postgres for production analytical workloads.
    """

    def __init__(self, *, database_url: str) -> None:
        self._path = _path_from_url(database_url)
        self._conn = None

    async def initialize(self) -> None:
        try:
            import aiosqlite
        except ImportError as exc:
            raise RuntimeError(
                "aiosqlite is required for SQLite persistence. "
                "Install with: pip install leanllm-ai[sqlite]"
            ) from exc

        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute(_CREATE_TABLE)
        for stmt in _CREATE_INDEXES:
            await self._conn.execute(stmt)
        await self._conn.commit()
        logger.info("[LeanLLM] SQLite event store ready (path=%s).", self._path)

    async def save(self, event: "LLMEvent") -> None:
        await self.save_batch([event])

    async def save_batch(self, events: "List[LLMEvent]") -> None:
        if not events or self._conn is None:
            return
        rows = [_to_row(e) for e in events]
        await self._conn.executemany(_INSERT, rows)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
