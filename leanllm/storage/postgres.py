from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ._hydrate import field_names, row_to_event
from .base import BaseEventStore

if TYPE_CHECKING:
    from ..events.models import LLMEvent

logger = logging.getLogger(__name__)

_INSERT = """
INSERT INTO llm_events (
    event_id, timestamp, model, provider,
    input_tokens, output_tokens, total_tokens,
    cost, latency_ms, labels, prompt, response, metadata, schema_version,
    correlation_id, parent_request_id, parameters, tools, tool_calls,
    time_to_first_token_ms, total_stream_time_ms, error_kind, error_message,
    normalized_input, normalized_output
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25)
ON CONFLICT (event_id) DO NOTHING
"""


def _to_row(event: "LLMEvent") -> tuple:
    return (
        event.event_id,
        event.timestamp,
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
        json.dumps(event.normalized_input.model_dump(mode="json"))
        if event.normalized_input
        else None,
        json.dumps(event.normalized_output.model_dump(mode="json"))
        if event.normalized_output
        else None,
    )


class PostgresEventStore(BaseEventStore):
    """
    asyncpg-backed event store with Alembic-managed schema.

    Optional dependency — install with: pip install leanllm-ai[postgres]
    """

    def __init__(self, *, database_url: str, auto_migrate: bool = True) -> None:
        self._url = database_url
        self._auto_migrate = auto_migrate
        self._pool = None

    async def initialize(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is required for PostgreSQL persistence. "
                "Install with: pip install leanllm-ai[postgres]"
            ) from exc

        if self._auto_migrate:
            try:
                from .migrations.runner import auto_migrate_postgres

                await auto_migrate_postgres(url=self._url)
            except Exception:
                logger.exception(
                    "[LeanLLM] Auto-migrate failed — will try to use existing schema."
                )

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
        logger.info("[LeanLLM] PostgreSQL event store ready.")

    async def save(self, event: "LLMEvent") -> None:
        await self.save_batch([event])

    async def save_batch(self, events: "List[LLMEvent]") -> None:
        if not events or self._pool is None:
            return
        rows = [_to_row(e) for e in events]
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT, rows)

    # ------------------------------------------------------------------
    # Read API (Module 12)
    # ------------------------------------------------------------------

    async def get_event(self, *, event_id: str) -> "Optional[LLMEvent]":
        if self._pool is None:
            return None
        select_cols = ", ".join(field_names())
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                f"SELECT {select_cols} FROM llm_events WHERE event_id = $1",
                event_id,
            )
        if record is None:
            return None
        try:
            return row_to_event(_record_to_dict(record))
        except Exception:
            logger.exception("[LeanLLM] Failed to hydrate event %s", event_id)
            return None

    async def list_events(
        self,
        *,
        correlation_id: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        errors_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> "List[LLMEvent]":
        if self._pool is None:
            return []
        where, params = _build_where(
            correlation_id=correlation_id,
            model=model,
            since=since,
            until=until,
            errors_only=errors_only,
        )
        select_cols = ", ".join(field_names())
        sql = (
            f"SELECT {select_cols} FROM llm_events {where} "
            f"ORDER BY timestamp DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
        )
        async with self._pool.acquire() as conn:
            records = await conn.fetch(sql, *params, limit, offset)
        events: List["LLMEvent"] = []
        for record in records:
            try:
                events.append(row_to_event(_record_to_dict(record)))
            except Exception:
                logger.exception(
                    "[LeanLLM] Skipping un-hydratable row event_id=%s",
                    record.get("event_id"),
                )
        return events

    async def count_events(
        self,
        *,
        correlation_id: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        errors_only: bool = False,
    ) -> int:
        if self._pool is None:
            return 0
        where, params = _build_where(
            correlation_id=correlation_id,
            model=model,
            since=since,
            until=until,
            errors_only=errors_only,
        )
        sql = f"SELECT COUNT(*) FROM llm_events {where}"
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(sql, *params)
        return int(value or 0)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None


def _record_to_dict(record: Any) -> Dict[str, Any]:
    """asyncpg.Record → plain dict, leaving JSONB values as native dicts/lists."""
    return dict(record)


def _build_where(
    *,
    correlation_id: Optional[str],
    model: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    errors_only: bool,
) -> Tuple[str, list]:
    """Build a WHERE clause + ordered params for asyncpg '$N' placeholders."""
    clauses: List[str] = []
    params: List[Any] = []
    if correlation_id is not None:
        params.append(correlation_id)
        clauses.append(f"correlation_id = ${len(params)}")
    if model is not None:
        params.append(model)
        clauses.append(f"model = ${len(params)}")
    if since is not None:
        params.append(since)
        clauses.append(f"timestamp >= ${len(params)}")
    if until is not None:
        params.append(until)
        clauses.append(f"timestamp <= ${len(params)}")
    if errors_only:
        clauses.append("error_kind IS NOT NULL")
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params
