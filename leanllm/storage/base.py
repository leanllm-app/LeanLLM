from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..events.models import LLMEvent


class BaseEventStore(ABC):
    """
    Abstract persistence backend.

    Concrete implementations must be safe to call from an asyncio event loop
    running in a background thread (the LeanLLM worker).
    """

    async def initialize(self) -> None:
        """Called once before the first write (create tables, open pool, etc.)."""

    @abstractmethod
    async def save(self, event: "LLMEvent") -> None:
        """Persist a single event."""

    @abstractmethod
    async def save_batch(self, events: "List[LLMEvent]") -> None:
        """Persist a batch of events efficiently."""

    @abstractmethod
    async def get_event(self, *, event_id: str) -> "Optional[LLMEvent]":
        """Fetch a single event by id, or None if it isn't present."""

    @abstractmethod
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
        """List events matching the given filters, newest first."""

    @abstractmethod
    async def count_events(
        self,
        *,
        correlation_id: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        errors_only: bool = False,
    ) -> int:
        """Count events matching the same filter shape as `list_events`."""

    async def close(self) -> None:
        """Release resources on shutdown."""
