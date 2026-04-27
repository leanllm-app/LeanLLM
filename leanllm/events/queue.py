from __future__ import annotations

import logging
import queue
from typing import List

from .models import LLMEvent

logger = logging.getLogger(__name__)


class EventQueue:
    """
    Thread-safe in-memory event buffer.

    Designed for single-producer (LLM request thread) /
    single-consumer (background worker thread) usage.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._q: queue.Queue[LLMEvent] = queue.Queue(maxsize=max_size)
        self._dropped = 0

    def enqueue(self, event: LLMEvent) -> bool:
        """Non-blocking put. Drops the event if the queue is full."""
        try:
            self._q.put_nowait(event)
            return True
        except queue.Full:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning(
                    "[LeanLLM] Event queue full — %d event(s) dropped so far.",
                    self._dropped,
                )
            return False

    def drain(self, batch_size: int) -> List[LLMEvent]:
        """Pull up to batch_size events without blocking."""
        events: List[LLMEvent] = []
        for _ in range(batch_size):
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        return events

    def drain_all(self) -> List[LLMEvent]:
        """Drain everything remaining in the queue (used on shutdown)."""
        events: List[LLMEvent] = []
        while True:
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        return events

    @property
    def dropped(self) -> int:
        return self._dropped

    def empty(self) -> bool:
        return self._q.empty()
