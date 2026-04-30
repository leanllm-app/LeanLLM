"""Module 15 — resilient delivery: configurable retry, jitter, budget cap,
drop counter, on_dropped callback, rate-limited drop log.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import List, Tuple

import pytest

from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.events.queue import EventQueue
from leanllm.events.worker import EventWorker
from leanllm.storage.base import BaseEventStore


def _ev(eid: str = "evt") -> LLMEvent:
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


class _FlakyStore(BaseEventStore):
    """Test store that fails the first N save_batch calls, then succeeds."""

    def __init__(self, *, fail_first_n: int = 0, ready: threading.Event | None = None):
        self.calls = 0
        self.batches: List[List[LLMEvent]] = []
        self._fail_first_n = fail_first_n
        self._ready = ready

    async def initialize(self) -> None:
        pass

    async def save(self, event):
        await self.save_batch([event])

    async def save_batch(self, events):
        self.calls += 1
        if self.calls <= self._fail_first_n:
            raise RuntimeError(f"forced failure {self.calls}")
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
        pass


# ----------------------------------------------------------------------
# Config wiring
# ----------------------------------------------------------------------


def test_config_defaults_for_module_15_fields():
    cfg = LeanLLMConfig()
    assert cfg.retry_max_attempts == 5
    assert cfg.retry_initial_backoff_ms == 500
    assert cfg.retry_total_budget_ms == 30_000


def test_from_env_reads_retry_fields(monkeypatch):
    for v in ("LEANLLM_API_KEY", "LEANLLM_DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    monkeypatch.setenv("LEANLLM_RETRY_MAX_ATTEMPTS", "10")
    monkeypatch.setenv("LEANLLM_RETRY_INITIAL_BACKOFF_MS", "100")
    monkeypatch.setenv("LEANLLM_RETRY_TOTAL_BUDGET_MS", "5000")
    cfg = LeanLLMConfig.from_env()
    assert cfg.retry_max_attempts == 10
    assert cfg.retry_initial_backoff_ms == 100
    assert cfg.retry_total_budget_ms == 5_000


# ----------------------------------------------------------------------
# Configurable retry attempts
# ----------------------------------------------------------------------


def test_max_retries_eventually_succeeds(caplog):
    queue = EventQueue(max_size=10)
    ready = threading.Event()
    store = _FlakyStore(fail_first_n=2, ready=ready)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=5,
        initial_backoff_ms=10,
        total_budget_ms=5_000,
    )
    worker.start()
    queue.enqueue(_ev("a"))
    assert ready.wait(timeout=2.0)
    worker.stop(timeout=2.0)
    assert store.calls == 3
    assert worker.dropped_events_count == 0
    assert len(store.batches) == 1
    assert store.batches[0][0].event_id == "a"


def test_max_retries_one_drops_on_first_failure():
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=1,
        initial_backoff_ms=10,
        total_budget_ms=5_000,
    )
    worker.start()
    queue.enqueue(_ev("only"))
    time.sleep(0.3)
    worker.stop(timeout=2.0)
    # max_retries=1 means 1 attempt total
    assert store.calls == 1
    assert worker.dropped_events_count == 1


# ----------------------------------------------------------------------
# Total budget cap drops early
# ----------------------------------------------------------------------


def test_total_budget_cap_drops_before_max_retries():
    """
    Tiny budget + small initial backoff → drop fires before all retries finish.

    With initial_backoff=100ms and budget=50ms:
      attempt 0 fails → next sleep would be ~100ms → exceeds budget → drop.
    """
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=10,
        initial_backoff_ms=100,
        total_budget_ms=50,
    )
    worker.start()
    queue.enqueue(_ev("a"))
    time.sleep(0.5)
    worker.stop(timeout=2.0)
    # Only 1 attempt landed because the projected next sleep blew the budget.
    assert store.calls == 1
    assert worker.dropped_events_count == 1


# ----------------------------------------------------------------------
# Jitter
# ----------------------------------------------------------------------


def test_jitter_applied_to_backoff(monkeypatch):
    """random.uniform should be invoked once per retry sleep."""
    seen_uniform_args: List[Tuple[float, float]] = []

    real_uniform = random.uniform

    def spy(a, b):
        seen_uniform_args.append((a, b))
        return real_uniform(a, b)

    monkeypatch.setattr(random, "uniform", spy)

    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=3,
        initial_backoff_ms=5,
        total_budget_ms=5_000,
    )
    worker.start()
    queue.enqueue(_ev("a"))
    time.sleep(0.3)
    worker.stop(timeout=2.0)
    # First two attempts fail and sleep with jitter; the 3rd is the last one
    # so it doesn't sleep before dropping.
    assert len(seen_uniform_args) >= 2
    # All calls should be the documented ±0.2 fraction.
    for a, b in seen_uniform_args:
        assert a == pytest.approx(-0.2)
        assert b == pytest.approx(0.2)


# ----------------------------------------------------------------------
# on_dropped callback
# ----------------------------------------------------------------------


def test_on_dropped_callback_fires_with_size_and_reason():
    seen: List[Tuple[int, str]] = []
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=2,
        initial_backoff_ms=10,
        total_budget_ms=5_000,
        on_dropped=lambda n, reason: seen.append((n, reason)),
    )
    worker.start()
    queue.enqueue(_ev("x"))
    queue.enqueue(_ev("y"))
    time.sleep(0.3)
    worker.stop(timeout=2.0)
    assert len(seen) == 1
    n, reason = seen[0]
    assert n == 2
    assert "max retries" in reason or "budget" in reason


def test_on_dropped_callback_failure_does_not_kill_worker(caplog):
    """A buggy callback shouldn't break event capture."""
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)

    def bad_callback(n, reason):
        raise RuntimeError("user callback exploded")

    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=1,
        initial_backoff_ms=10,
        on_dropped=bad_callback,
    )
    worker.start()
    queue.enqueue(_ev("x"))
    time.sleep(0.3)
    worker.stop(timeout=2.0)
    # Worker should still record the drop, not crash.
    assert worker.dropped_events_count == 1


# ----------------------------------------------------------------------
# Rate-limited drop log
# ----------------------------------------------------------------------


def test_drop_log_first_drop_emits_immediately(caplog):
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=1,
        initial_backoff_ms=10,
    )
    worker.start()
    queue.enqueue(_ev("a"))
    with caplog.at_level(logging.ERROR):
        time.sleep(0.3)
        worker.stop(timeout=2.0)
    msgs = [r.message for r in caplog.records if "Dropped" in r.message]
    assert any("Dropped 1 event(s)" in m for m in msgs)


def test_drop_log_rate_limited_within_window(caplog, monkeypatch):
    """Within the 60s window, only the first drop emits an ERROR log line."""
    queue = EventQueue(max_size=10)
    store = _FlakyStore(fail_first_n=999)
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=10,
        flush_interval_ms=20,
        max_retries=1,
        initial_backoff_ms=5,
    )
    worker.start()
    with caplog.at_level(logging.ERROR):
        for i in range(5):
            queue.enqueue(_ev(f"e{i}"))
            time.sleep(0.05)
        time.sleep(0.4)
        worker.stop(timeout=2.0)

    drop_logs = [r for r in caplog.records if "Dropped" in r.message]
    # First drop logs; subsequent drops within the same 60s window do not.
    assert len(drop_logs) == 1
    # All 5 events should be counted in dropped_events_count.
    assert worker.dropped_events_count == 5


# ----------------------------------------------------------------------
# LeanLLM client surface — properties + on_dropped_events kwarg
# ----------------------------------------------------------------------


def test_client_dropped_events_count_aggregates_queue_and_worker():
    seen: List[Tuple[int, str]] = []
    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
        batch_size=10,
        retry_max_attempts=1,
        retry_initial_backoff_ms=10,
        queue_max_size=2,  # tiny queue → easy to overflow
    )
    client = LeanLLM(
        api_key="sk-test",
        config=config,
        on_dropped_events=lambda n, reason: seen.append((n, reason)),
    )
    try:
        # Fill + overflow the queue from the request thread (queue-full drops).
        for i in range(5):
            client._queue.enqueue(_ev(f"q{i}"))
        # The store is real SQLite in-memory — saves succeed, no worker drops.
        # But we set queue_max_size=2 so queue drops some events.
        time.sleep(0.3)
        # Expect queue drops > 0; worker drops = 0.
        assert client._queue.dropped >= 1
        assert client.dropped_events_count == client._queue.dropped
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)


def test_client_events_in_flight_combines_queue_and_inflight(monkeypatch):
    config = LeanLLMConfig(enable_persistence=False)
    client = LeanLLM(api_key="sk-test", config=config)
    # No persistence → no queue / worker → in-flight is always 0.
    assert client.events_in_flight == 0


def test_client_on_dropped_events_kwarg_propagates_to_worker(monkeypatch):
    """The on_dropped_events kwarg on LeanLLM reaches the underlying EventWorker."""
    seen: List[Tuple[int, str]] = []
    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
        batch_size=10,
        retry_max_attempts=1,
        retry_initial_backoff_ms=10,
    )
    client = LeanLLM(
        api_key="sk-test",
        config=config,
        on_dropped_events=lambda n, reason: seen.append((n, reason)),
    )
    try:
        # Worker's callback should be the same object we passed in.
        assert client._worker._on_dropped is not None
        # Drive the drop path directly by calling the worker helper synchronously
        # via the worker's own loop.
        import asyncio as _asyncio

        class _AlwaysFail:
            async def save_batch(self, events):
                raise RuntimeError("forced")

            async def close(self):  # called by worker on graceful shutdown
                pass

        # Swap the store on the worker for a guaranteed-to-fail one.
        client._worker._store = _AlwaysFail()

        async def _flush():
            await client._worker._flush_with_retry([_ev("doomed")])

        future = _asyncio.run_coroutine_threadsafe(_flush(), client._worker._loop)
        future.result(timeout=2.0)
        assert seen == [(1, seen[0][1])]
        assert "max retries" in seen[0][1] or "budget" in seen[0][1]
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)
