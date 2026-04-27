from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, List

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
        json.dumps(event.normalized_input.model_dump(mode="json")) if event.normalized_input else None,
        json.dumps(event.normalized_output.model_dump(mode="json")) if event.normalized_output else None,
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

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
