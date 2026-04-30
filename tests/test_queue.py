from __future__ import annotations

from leanllm import LLMEvent
from leanllm.events.queue import EventQueue


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


def test_enqueue_returns_true_when_space_available_and_empty_flips():
    q = EventQueue(max_size=10)
    assert q.empty() is True
    assert q.enqueue(_ev("a")) is True
    assert q.empty() is False


def test_drain_pulls_up_to_batch_size_in_fifo():
    q = EventQueue(max_size=10)
    for i in range(5):
        q.enqueue(_ev(f"e{i}"))
    drained = q.drain(3)
    assert [e.event_id for e in drained] == ["e0", "e1", "e2"]
    rest = q.drain_all()
    assert [e.event_id for e in rest] == ["e3", "e4"]


def test_drain_all_empties_the_queue():
    q = EventQueue(max_size=10)
    for i in range(3):
        q.enqueue(_ev(f"e{i}"))
    drained = q.drain_all()
    assert len(drained) == 3
    assert q.empty() is True


def test_enqueue_at_capacity_returns_false_and_increments_dropped():
    q = EventQueue(max_size=2)
    assert q.enqueue(_ev("a")) is True
    assert q.enqueue(_ev("b")) is True
    assert q.enqueue(_ev("c")) is False
    assert q.dropped == 1
    assert q.enqueue(_ev("d")) is False
    assert q.dropped == 2
