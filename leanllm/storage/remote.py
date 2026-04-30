from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from .base import BaseEventStore

if TYPE_CHECKING:
    from ..events.models import LLMEvent


_REMOTE_READS_DEFERRED = (
    "Remote read API (get_event / list_events / count_events) requires the "
    "LeanLLM SaaS endpoint to expose GET /v1/events. This is on the SaaS "
    "roadmap and not implemented in the OSS SDK yet — use a Postgres or "
    "SQLite backend if you need to query events back."
)

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://api.leanllm.dev"


class RemoteEventStore(BaseEventStore):
    """
    Sends event batches to the LeanLLM Service via POST /v1/events.

    The lib's worker already handles batching and flush policy —
    this store just ships the batch as-is over HTTP.
    """

    def __init__(self, *, api_key: str, endpoint: str = _DEFAULT_ENDPOINT) -> None:
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._url = f"{self._endpoint}/v1/events"
        self._client = None

    async def initialize(self) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for remote persistence. "
                "Install with: pip install leanllm-ai[remote]"
            ) from exc

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        logger.info("[LeanLLM] Remote event store ready (endpoint=%s).", self._endpoint)

    async def save(self, event: "LLMEvent") -> None:
        await self.save_batch([event])

    async def save_batch(self, events: "List[LLMEvent]") -> None:
        if not events or self._client is None:
            return

        payload = {"events": [event.model_dump(mode="json") for event in events]}

        response = await self._client.post(self._url, content=json.dumps(payload))
        response.raise_for_status()

        body = response.json()
        accepted = body.get("accepted", 0)
        dropped = body.get("dropped", 0)

        if dropped > 0:
            logger.warning(
                "[LeanLLM] Service accepted %d, dropped %d events.", accepted, dropped
            )

    # ------------------------------------------------------------------
    # Read API (Module 12) — deferred until SaaS exposes GET /v1/events
    # ------------------------------------------------------------------

    async def get_event(self, *, event_id: str) -> "Optional[LLMEvent]":
        raise NotImplementedError(_REMOTE_READS_DEFERRED)

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
        raise NotImplementedError(_REMOTE_READS_DEFERRED)

    async def count_events(
        self,
        *,
        correlation_id: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        errors_only: bool = False,
    ) -> int:
        raise NotImplementedError(_REMOTE_READS_DEFERRED)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
