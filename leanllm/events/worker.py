from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..storage.base import BaseEventStore
    from .queue import EventQueue
    from .models import LLMEvent

logger = logging.getLogger(__name__)


class EventWorker:
    """
    Background worker that drains EventQueue and batch-inserts into a store.

    Runs an asyncio event loop in a dedicated daemon thread so it never
    touches the main request thread.

    Flush policy: every N events  OR  every T milliseconds — whichever
    comes first.
    """

    def __init__(
        self,
        queue: "EventQueue",
        store: "BaseEventStore",
        batch_size: int = 100,
        flush_interval_ms: int = 200,
    ) -> None:
        self._queue = queue
        self._store = store
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000.0

        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="leanllm-worker"
        )
        self._thread.start()
        atexit.register(self.stop)

    def stop(self, timeout: float = 3.0) -> None:
        """Signal the worker to stop and wait for it to finish flushing."""
        self._running = False
        if self._loop and not self._loop.is_closed() and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Thread entry-point
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker_loop())
        finally:
            self._loop.close()

    # ------------------------------------------------------------------
    # Async worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        self._stop_event = asyncio.Event()

        try:
            await self._store.initialize()
        except Exception:
            logger.exception("[LeanLLM] Failed to initialize event store — persistence disabled.")
            return

        logger.info(
            "[LeanLLM] Event worker started (batch=%d, interval=%dms).",
            self._batch_size,
            int(self._flush_interval * 1000),
        )

        while not self._stop_event.is_set():
            await self._tick()
            # Sleep for flush_interval, but wake early on stop signal
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self._flush_interval,
                )
            except asyncio.TimeoutError:
                pass

        # Final drain on graceful shutdown
        remaining = self._queue.drain_all()
        if remaining:
            logger.info("[LeanLLM] Flushing %d remaining event(s) on shutdown.", len(remaining))
            await self._flush_with_retry(remaining)

        await self._store.close()
        logger.info("[LeanLLM] Event worker stopped.")

    async def _tick(self) -> None:
        events = self._queue.drain(self._batch_size)
        if events:
            await self._flush_with_retry(events)

    async def _flush_with_retry(self, events: "List[LLMEvent]", max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                await self._store.save_batch(events)
                logger.debug("[LeanLLM] Saved batch of %d event(s).", len(events))
                return True
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(
                        "[LeanLLM] Batch write failed (attempt %d/%d): %s — retrying in %.1fs.",
                        attempt + 1, max_retries, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "[LeanLLM] Dropping %d event(s) after %d failed attempts: %s",
                        len(events), max_retries, exc,
                    )
        return False
