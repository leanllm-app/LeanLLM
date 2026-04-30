from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.events.models import ErrorKind
from leanllm.normalizer import (
    InputType,
    LengthBucket,
    NormalizedInput,
)
from leanllm.storage.remote import RemoteEventStore
from leanllm.storage.sqlite import SQLiteEventStore


def _ev(
    *,
    event_id: str = "evt-1",
    correlation_id: str | None = None,
    parent_request_id: str | None = None,
    model: str = "gpt-4o-mini",
    error_kind: ErrorKind | None = None,
    timestamp: datetime | None = None,
    normalized_input: NormalizedInput | None = None,
    tool_calls=None,
) -> LLMEvent:
    return LLMEvent(
        event_id=event_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        correlation_id=correlation_id,
        parent_request_id=parent_request_id,
        model=model,
        provider="openai",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cost=0.001,
        latency_ms=10,
        labels={"team": "qa"},
        prompt=json.dumps([{"role": "user", "content": "hi"}]),
        response="hello",
        parameters={"temperature": 0.5},
        tools=[{"type": "function"}],
        tool_calls=tool_calls,
        error_kind=error_kind,
        error_message="boom" if error_kind else None,
        normalized_input=normalized_input,
    )


# ----------------------------------------------------------------------
# SQLiteEventStore.get_event / list_events / count_events
# ----------------------------------------------------------------------


async def test_sqlite_get_event_returns_none_for_missing():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    assert await store.get_event(event_id="missing") is None
    await store.close()


async def test_sqlite_get_event_round_trips_basic_fields():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    ev = _ev(event_id="rt-1", correlation_id="C1")
    await store.save_batch([ev])

    fetched = await store.get_event(event_id="rt-1")
    assert fetched is not None
    assert fetched.event_id == "rt-1"
    assert fetched.correlation_id == "C1"
    assert fetched.model == "gpt-4o-mini"
    assert fetched.labels == {"team": "qa"}
    assert fetched.parameters == {"temperature": 0.5}
    assert fetched.tools == [{"type": "function"}]
    assert fetched.error_kind is None
    await store.close()


async def test_sqlite_get_event_round_trips_normalized_pydantic_nested():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    ev = _ev(
        event_id="rt-norm",
        normalized_input=NormalizedInput(
            input_type=InputType.CHAT,
            language="latin",
            length_bucket=LengthBucket.SHORT,
            semantic_hash="abc123",
        ),
    )
    await store.save_batch([ev])
    fetched = await store.get_event(event_id="rt-norm")
    assert fetched is not None
    assert fetched.normalized_input is not None
    assert fetched.normalized_input.input_type == InputType.CHAT
    assert fetched.normalized_input.semantic_hash == "abc123"
    await store.close()


async def test_sqlite_get_event_round_trips_error_kind_enum():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch([_ev(event_id="err-1", error_kind=ErrorKind.RATE_LIMIT)])
    fetched = await store.get_event(event_id="err-1")
    assert fetched.error_kind == ErrorKind.RATE_LIMIT
    assert fetched.error_message == "boom"
    await store.close()


async def test_sqlite_list_events_orders_by_timestamp_desc():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        await store.save_batch(
            [_ev(event_id=f"e{i}", timestamp=base + timedelta(minutes=i))]
        )
    listed = await store.list_events(limit=10)
    assert [e.event_id for e in listed] == ["e2", "e1", "e0"]
    await store.close()


async def test_sqlite_list_events_filters_by_correlation_id():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch(
        [
            _ev(event_id="a", correlation_id="C1"),
            _ev(event_id="b", correlation_id="C2"),
            _ev(event_id="c", correlation_id="C1"),
        ]
    )
    listed = await store.list_events(correlation_id="C1")
    assert {e.event_id for e in listed} == {"a", "c"}
    await store.close()


async def test_sqlite_list_events_filters_by_model():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch(
        [
            _ev(event_id="a", model="gpt-4o-mini"),
            _ev(event_id="b", model="gpt-4o"),
        ]
    )
    listed = await store.list_events(model="gpt-4o")
    assert [e.event_id for e in listed] == ["b"]
    await store.close()


async def test_sqlite_list_events_filters_by_time_range():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    await store.save_batch(
        [
            _ev(event_id="early", timestamp=base),
            _ev(event_id="mid", timestamp=base + timedelta(hours=1)),
            _ev(event_id="late", timestamp=base + timedelta(hours=2)),
        ]
    )
    listed = await store.list_events(
        since=base + timedelta(minutes=30),
        until=base + timedelta(minutes=90),
    )
    assert [e.event_id for e in listed] == ["mid"]
    await store.close()


async def test_sqlite_list_events_errors_only_filter():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch(
        [
            _ev(event_id="ok"),
            _ev(event_id="err", error_kind=ErrorKind.TIMEOUT),
        ]
    )
    listed = await store.list_events(errors_only=True)
    assert [e.event_id for e in listed] == ["err"]
    await store.close()


async def test_sqlite_list_events_limit_and_offset_paginate():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        await store.save_batch(
            [_ev(event_id=f"e{i}", timestamp=base + timedelta(minutes=i))]
        )

    page1 = await store.list_events(limit=2, offset=0)
    page2 = await store.list_events(limit=2, offset=2)
    assert [e.event_id for e in page1] == ["e4", "e3"]
    assert [e.event_id for e in page2] == ["e2", "e1"]
    await store.close()


async def test_sqlite_count_events_matches_filter():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch(
        [
            _ev(event_id="a", correlation_id="C"),
            _ev(event_id="b", correlation_id="C"),
            _ev(event_id="c", correlation_id="OTHER"),
        ]
    )
    assert await store.count_events() == 3
    assert await store.count_events(correlation_id="C") == 2
    await store.close()


async def test_sqlite_get_event_uninitialized_returns_none():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    assert await store.get_event(event_id="x") is None
    assert await store.list_events() == []
    assert await store.count_events() == 0


# ----------------------------------------------------------------------
# Hydration error path: corrupt rows are skipped, not crash the query
# ----------------------------------------------------------------------


async def test_sqlite_list_events_skips_corrupt_row(caplog):
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    await store.save_batch([_ev(event_id="good")])
    # corrupt a row directly
    await store._conn.execute(
        "UPDATE llm_events SET parameters = ? WHERE event_id = ?",
        ("not-json{", "good"),
    )
    await store._conn.commit()
    with caplog.at_level("ERROR"):
        listed = await store.list_events()
    assert listed == []
    assert any("Skipping un-hydratable row" in m for m in caplog.messages)
    await store.close()


# ----------------------------------------------------------------------
# RemoteEventStore — read API stubs
# ----------------------------------------------------------------------


async def test_remote_get_event_raises_not_implemented():
    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    with pytest.raises(NotImplementedError, match="SaaS endpoint"):
        await store.get_event(event_id="anything")


async def test_remote_list_events_raises_not_implemented():
    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    with pytest.raises(NotImplementedError):
        await store.list_events()


async def test_remote_count_events_raises_not_implemented():
    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    with pytest.raises(NotImplementedError):
        await store.count_events()


# ----------------------------------------------------------------------
# LeanLLM public surface — cross-thread on the worker loop
# ----------------------------------------------------------------------


def _wait_until(predicate, timeout=2.0, interval=0.02):
    import time as _t

    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if predicate():
            return True
        _t.sleep(interval)
    return False


async def test_client_get_event_after_persistence_round_trip():
    # Persistence enabled with SQLite in-memory; worker writes the event,
    # then the client reads it back.
    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
        batch_size=1,
    )
    client = LeanLLM(api_key="sk-test", config=config)
    try:
        # Manually enqueue an event (skipping the chat path — that's tested
        # elsewhere; here we just want to exercise get_event end-to-end).
        ev = _ev(event_id="client-1", correlation_id="C")
        client._queue.enqueue(ev)
        # Wait for the worker daemon to flush.
        assert _wait_until(
            lambda: (
                asyncio.run_coroutine_threadsafe(
                    client._store.count_events(),
                    client._worker._loop,
                ).result(timeout=1.0)
                >= 1
            ),
        )
        fetched = await client.get_event(event_id="client-1")
        assert fetched is not None
        assert fetched.event_id == "client-1"
        assert fetched.correlation_id == "C"
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)


async def test_client_get_event_when_persistence_disabled_raises():
    client = LeanLLM(
        api_key="sk-test",
        config=LeanLLMConfig(enable_persistence=False),
    )
    with pytest.raises(RuntimeError, match="persistence is disabled"):
        await client.get_event(event_id="anything")


async def test_client_list_events_passes_filters_through():
    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
        batch_size=10,
    )
    client = LeanLLM(api_key="sk-test", config=config)
    try:
        client._queue.enqueue(_ev(event_id="a", correlation_id="C1"))
        client._queue.enqueue(_ev(event_id="b", correlation_id="C2"))
        # Wait for flush to land both
        assert _wait_until(
            lambda: (
                asyncio.run_coroutine_threadsafe(
                    client._store.count_events(),
                    client._worker._loop,
                ).result(timeout=1.0)
                == 2
            ),
        )
        listed = await client.list_events(correlation_id="C1")
        assert [e.event_id for e in listed] == ["a"]
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)


# ----------------------------------------------------------------------
# ReplayEngine.replay_by_id
# ----------------------------------------------------------------------


async def test_replay_by_id_fetches_then_replays(monkeypatch):
    from types import SimpleNamespace

    from leanllm import ReplayEngine

    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
        batch_size=1,
    )
    client = LeanLLM(api_key="sk-test", config=config)
    try:
        ev = _ev(event_id="for-replay")
        client._queue.enqueue(ev)
        assert _wait_until(
            lambda: (
                asyncio.run_coroutine_threadsafe(
                    client._store.count_events(),
                    client._worker._loop,
                ).result(timeout=1.0)
                >= 1
            ),
        )

        # mock chat_completion so replay() can run without hitting the network
        message = SimpleNamespace(content="replay text", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        response = SimpleNamespace(choices=[choice], usage=usage)
        monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)

        engine = ReplayEngine(client=client)
        result = await engine.replay_by_id(event_id="for-replay")
        assert result.original_request_id == "for-replay"
        assert result.text_after == "replay text"
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)


async def test_replay_by_id_raises_when_event_not_found():
    from leanllm import ReplayEngine

    config = LeanLLMConfig(
        database_url="sqlite:///:memory:",
        leanllm_api_key=None,
        enable_persistence=True,
        flush_interval_ms=20,
    )
    client = LeanLLM(api_key="sk-test", config=config)
    try:
        engine = ReplayEngine(client=client)
        with pytest.raises(ValueError, match="not found"):
            await engine.replay_by_id(event_id="does-not-exist")
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)
