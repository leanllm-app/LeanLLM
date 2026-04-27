from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..events.models import LLMEvent


class BaseEventStore(ABC):
    """
    Abstract persistence backend.

    Concrete implementations must be safe to call from an asyncio event loop
    running in a background thread.
    """

    async def initialize(self) -> None:
        """Called once before the first write (create tables, open pool, etc.)."""

    @abstractmethod
    async def save(self, event: "LLMEvent") -> None:
        """Persist a single event."""

    @abstractmethod
    async def save_batch(self, events: "List[LLMEvent]") -> None:
        """Persist a batch of events efficiently."""

    async def close(self) -> None:
        """Release resources on shutdown."""
