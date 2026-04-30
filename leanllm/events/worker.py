from __future__ import annotations

import asyncio
import atexit
import logging
import random
import threading
import time
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:
    from ..storage.base import BaseEventStore
    from .queue import EventQueue
    from .models import LLMEvent

logger = logging.getLogger(__name__)


# Rate-limited drop log: emit at most once per this many seconds, with the
# cumulative count of events dropped since the last log line. Avoids log spam
# during sustained outages while keeping the drop signal visible.
_DROP_LOG_WINDOW_SECONDS = 60.0


# Jitter range applied to each backoff sleep: wait * (1 + uniform(-0.2, +0.2)).
# Spreads retries across replicas so a recovering store isn't hammered.
_JITTER_FRACTION = 0.2


class EventWorker:
    """
    Background worker that drains EventQueue and batch-inserts into a store.

    Runs an asyncio event loop in a dedicated daemon thread so it never
    touches the main request thread.

    Flush policy: every N events  OR  every T milliseconds — whichever
    comes first.

    Resilience (Module 15): in-memory only — no disk spillover. On batch save
    failure, retry up to `max_retries` with exponential backoff + jitter, capped
    by `total_budget_ms`. Once exhausted, drop the batch with a rate-limited
    WARNING log and bump a visible counter. Optional `on_dropped` callback hooks
    into Sentry / Datadog / Prometheus without us pulling those deps in.
    """

    def __init__(
        self,
        queue: "EventQueue",
        store: "BaseEventStore",
        batch_size: int = 100,
        flush_interval_ms: int = 200,
        *,
        max_retries: int = 5,
        initial_backoff_ms: int = 500,
        total_budget_ms: int = 30_000,
        on_dropped: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self._queue = queue
        self._store = store
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000.0

        self._max_retries = max(1, max_retries)
        self._initial_backoff = max(0.0, initial_backoff_ms / 1000.0)
        self._total_budget = max(0.0, total_budget_ms / 1000.0)
        self._on_dropped = on_dropped

        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._loop_ready = threading.Event()

        # Module 15 — observability counters. Read from any thread.
        self._dropped_events: int = 0
        self._dropped_batches: int = 0
        self._inflight_count: int = 0

        # Rate-limited drop log state.
        self._last_drop_log_ts: float = 0.0
        self._dropped_since_last_log: int = 0

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="leanllm-worker"
        )
        self._thread.start()
        # Block until the daemon thread has created its event loop, so callers
        # of run_coroutine_threadsafe never see `_loop is None`. Bounded wait
        # — if the thread never spins up something is very wrong.
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("[LeanLLM] event worker failed to start within 5s")
        atexit.register(self.stop)

    def stop(self, timeout: float = 3.0) -> None:
        """Signal the worker to stop and wait for it to finish flushing."""
        self._running = False
        if self._loop and not self._loop.is_closed() and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Observability (Module 15) — read from any thread.
    # ------------------------------------------------------------------

    @property
    def dropped_events_count(self) -> int:
        return self._dropped_events

    @property
    def dropped_batches_count(self) -> int:
        return self._dropped_batches

    @property
    def inflight_count(self) -> int:
        return self._inflight_count

    # ------------------------------------------------------------------
    # Thread entry-point
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
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
            logger.exception(
                "[LeanLLM] Failed to initialize event store — persistence disabled."
            )
            return

        logger.info(
            "[LeanLLM] Event worker started (batch=%d, interval=%dms, retries=%d).",
            self._batch_size,
            int(self._flush_interval * 1000),
            self._max_retries,
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
            logger.info(
                "[LeanLLM] Flushing %d remaining event(s) on shutdown.", len(remaining)
            )
            await self._flush_with_retry(remaining)

        await self._store.close()
        logger.info("[LeanLLM] Event worker stopped.")

    async def _tick(self) -> None:
        events = self._queue.drain(self._batch_size)
        if events:
            await self._flush_with_retry(events)

    async def _flush_with_retry(self, events: "List[LLMEvent]") -> bool:
        """Save a batch with bounded retry. Drops the batch if exhausted."""
        self._inflight_count = len(events)
        elapsed = 0.0
        last_exc: Optional[BaseException] = None
        try:
            for attempt in range(self._max_retries):
                try:
                    await self._store.save_batch(events)
                    logger.debug("[LeanLLM] Saved batch of %d event(s).", len(events))
                    return True
                except Exception as exc:
                    last_exc = exc
                    if attempt >= self._max_retries - 1:
                        # Last attempt — drop.
                        self._record_drop(
                            events,
                            reason=f"max retries reached: {exc}",
                        )
                        return False

                    base_wait = self._initial_backoff * (2**attempt)
                    jitter = random.uniform(-_JITTER_FRACTION, _JITTER_FRACTION)
                    wait = max(0.0, base_wait * (1 + jitter))

                    if elapsed + wait > self._total_budget:
                        # Would exceed total budget — drop now instead of
                        # holding the worker hostage for a stuck downstream.
                        self._record_drop(
                            events,
                            reason=f"retry budget exceeded after {attempt + 1} attempts: {exc}",
                        )
                        return False

                    logger.warning(
                        "[LeanLLM] Batch write failed (attempt %d/%d): %s — retrying in %.2fs.",
                        attempt + 1,
                        self._max_retries,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    elapsed += wait
            # Defensive — shouldn't reach here. last_exc preserved if it does.
            if last_exc is not None:
                self._record_drop(events, reason=f"unexpected: {last_exc}")
            return False
        finally:
            self._inflight_count = 0

    def _record_drop(self, events: "List[LLMEvent]", *, reason: str) -> None:
        """Update counters, fire callback, and emit a rate-limited log line."""
        n = len(events)
        self._dropped_events += n
        self._dropped_batches += 1
        self._dropped_since_last_log += n

        now = time.monotonic()
        # _last_drop_log_ts==0 means "never logged" → always log on first drop.
        if now - self._last_drop_log_ts >= _DROP_LOG_WINDOW_SECONDS:
            logger.error(
                "[LeanLLM] Dropped %d event(s) since last log (latest reason: %s).",
                self._dropped_since_last_log,
                reason,
            )
            self._last_drop_log_ts = now
            self._dropped_since_last_log = 0

        if self._on_dropped is not None:
            try:
                self._on_dropped(n, reason)
            except Exception:
                logger.exception(
                    "[LeanLLM] on_dropped callback raised; swallowing to keep worker alive.",
                )
