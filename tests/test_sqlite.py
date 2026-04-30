from __future__ import annotations


import pytest

from leanllm import LLMEvent
from leanllm.storage.sqlite import SQLiteEventStore, _path_from_url


def _ev(eid: str = "evt-1", *, model: str = "gpt-4o-mini") -> LLMEvent:
    return LLMEvent(
        event_id=eid,
        model=model,
        provider="openai",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cost=0.001,
        latency_ms=10,
    )


# ----------------------------------------------------------------------
# URL parsing
# ----------------------------------------------------------------------


def test_path_from_url_memory_alias():
    assert _path_from_url("sqlite:///:memory:") == ":memory:"
    assert _path_from_url("sqlite://") == ":memory:"


def test_path_from_url_absolute():
    assert _path_from_url("sqlite:////abs/path.db") == "/abs/path.db"


def test_path_from_url_relative():
    assert _path_from_url("sqlite:///./rel.db") == "./rel.db"


# ----------------------------------------------------------------------
# Round-trip via SELECT
# ----------------------------------------------------------------------


async def test_save_batch_persists_event_and_select_round_trip():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    ev = _ev("evt-rt")
    await store.save_batch([ev])

    cursor = await store._conn.execute(
        "SELECT event_id, model, total_tokens, cost FROM llm_events WHERE event_id = ?",
        ("evt-rt",),
    )
    row = await cursor.fetchone()
    assert row[0] == "evt-rt"
    assert row[1] == "gpt-4o-mini"
    assert row[2] == 3
    assert row[3] == pytest.approx(0.001)
    await store.close()


async def test_save_batch_empty_or_uninitialized_is_noop():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    # uninitialized → no error
    await store.save_batch([_ev()])

    await store.initialize()
    # empty list → no error, nothing inserted
    await store.save_batch([])
    cursor = await store._conn.execute("SELECT COUNT(*) FROM llm_events")
    row = await cursor.fetchone()
    assert row[0] == 0
    await store.close()


async def test_insert_or_ignore_makes_duplicates_idempotent():
    store = SQLiteEventStore(database_url="sqlite:///:memory:")
    await store.initialize()
    ev = _ev("dup-1")
    await store.save_batch([ev])
    await store.save_batch([ev])
    cursor = await store._conn.execute(
        "SELECT COUNT(*) FROM llm_events WHERE event_id = ?",
        ("dup-1",),
    )
    row = await cursor.fetchone()
    assert row[0] == 1
    await store.close()
