from __future__ import annotations

import threading
import time
from typing import List

from leanllm import LLMEvent
from leanllm.events.queue import EventQueue
from leanllm.events.worker import EventWorker
from leanllm.storage.base import BaseEventStore


def _ev(eid: str) -> LLMEvent:
    return LLMEvent(
        event_id=eid,
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=0.0,
        latency_ms=0,
    )


class _RecordingStore(BaseEventStore):
    """Captures save_batch calls; sets a flag-event when first batch arrives."""

    def __init__(
        self, *, fail_first_n: int = 0, ready_event: threading.Event | None = None
    ):
        self.batches: List[List[LLMEvent]] = []
        self._fail_first_n = fail_first_n
        self._calls = 0
        self._ready = ready_event
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def save(self, event):  # not used by worker, but required by ABC
        await self.save_batch([event])

    async def save_batch(self, events):
        self._calls += 1
        if self._calls <= self._fail_first_n:
            raise RuntimeError(f"forced failure {self._calls}")
        self.batches.append(list(events))
        if self._ready is not None:
            self._ready.set()

    async def get_event(self, *, event_id):
        return None

    async def list_events(self, **_):
        return []

    async def count_events(self, **_):
        return 0

    async def close(self) -> None:
        self.closed = True


def _drain_within(
    *, store: _RecordingStore, ready: threading.Event, timeout: float = 2.0
) -> bool:
    return ready.wait(timeout=timeout)


def test_worker_drains_enqueued_events_into_store():
    queue = EventQueue(max_size=100)
    ready = threading.Event()
    store = _RecordingStore(ready_event=ready)
    worker = EventWorker(queue=queue, store=store, batch_size=10, flush_interval_ms=20)
    worker.start()

    for i in range(3):
        queue.enqueue(_ev(f"e{i}"))

    assert _drain_within(store=store, ready=ready)
    worker.stop(timeout=2.0)
    assert any(len(batch) > 0 for batch in store.batches)
    assert store.closed is True


def test_worker_retries_failing_save_batch_then_drops():
    queue = EventQueue(max_size=100)
    ready = threading.Event()
    store = _RecordingStore(fail_first_n=999, ready_event=ready)
    # Tight retry window for fast tests: 3 attempts, 10ms base backoff.
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=3,
        initial_backoff_ms=10,
        total_budget_ms=5_000,
    )
    worker.start()
    queue.enqueue(_ev("only"))
    # let the worker exhaust attempts: 10ms + 20ms = 30ms of sleeps + 3 calls.
    time.sleep(0.5)
    worker.stop(timeout=2.0)
    assert store._calls >= 3
    assert store.batches == []
    # Module 15 — drop counter + batch counter exposed.
    assert worker.dropped_events_count == 1
    assert worker.dropped_batches_count == 1


def test_worker_flushes_remaining_events_on_graceful_stop():
    queue = EventQueue(max_size=100)
    ready = threading.Event()
    store = _RecordingStore(ready_event=ready)
    # large flush_interval — only stop-time drain should land the events
    worker = EventWorker(
        queue=queue, store=store, batch_size=100, flush_interval_ms=10_000
    )
    worker.start()
    queue.enqueue(_ev("a"))
    queue.enqueue(_ev("b"))
    worker.stop(timeout=3.0)
    flat = [e.event_id for batch in store.batches for e in batch]
    assert set(flat) >= {"a", "b"}
