"""Module 16 — DX helpers: pretty_print, last_event/recent_events, init, auto_chain."""

from __future__ import annotations

import asyncio
import io
from typing import List

import pytest

import leanllm
from leanllm import (
    LeanLLM,
    LeanLLMConfig,
    LLMEvent,
    ReplayResult,
    trace,
)
from leanllm.context import (
    get_auto_chain_parent,
    set_auto_chain_parent,
)
from leanllm.events.models import ErrorKind
from tests.conftest import make_response


# ----------------------------------------------------------------------
# LLMEvent.pretty_print
# ----------------------------------------------------------------------


def _success_event(**overrides) -> LLMEvent:
    base = dict(
        event_id="evt-1",
        model="gpt-4o",
        provider="openai",
        input_tokens=5,
        output_tokens=10,
        total_tokens=15,
        cost=0.0023,
        latency_ms=1400,
        prompt='[{"role":"user","content":"hello"}]',
        response="hi there",
    )
    base.update(overrides)
    return LLMEvent(**base)


def test_pretty_print_renders_header_and_token_block():
    buf = io.StringIO()
    _success_event().pretty_print(file=buf)
    text = buf.getvalue()
    assert "Event evt-1" in text
    assert "[OK]" in text
    assert "tokens:    in=5  out=10  total=15" in text
    assert "cost:      $0.002300" in text
    assert "latency:   1400ms" in text


def test_pretty_print_includes_optional_correlation_and_environment():
    ev = _success_event(
        correlation_id="C1",
        parent_request_id="P1",
        metadata={"environment": "production"},
    )
    buf = io.StringIO()
    ev.pretty_print(file=buf)
    text = buf.getvalue()
    assert "correlation: C1" in text
    assert "parent:      P1" in text
    assert "environment: production" in text


def test_pretty_print_truncates_long_prompt_and_response():
    long_text = "x" * 2000
    ev = _success_event(prompt=long_text, response=long_text)
    buf = io.StringIO()
    ev.pretty_print(file=buf, truncate=100)
    text = buf.getvalue()
    assert "<truncated 1900 chars>" in text


def test_pretty_print_truncate_none_disables_truncation():
    long_text = "x" * 2000
    ev = _success_event(prompt=long_text, response=long_text)
    buf = io.StringIO()
    ev.pretty_print(file=buf, truncate=None)
    text = buf.getvalue()
    assert "truncated" not in text
    assert long_text in text


def test_pretty_print_omits_prompt_response_sections_when_none():
    ev = _success_event(prompt=None, response=None)
    buf = io.StringIO()
    ev.pretty_print(file=buf)
    text = buf.getvalue()
    assert "── input ──" not in text
    assert "── output ──" not in text


def test_pretty_print_renders_tool_calls():
    ev = _success_event(
        tool_calls=[{"id": "c1", "function": {"name": "search", "arguments": "{}"}}],
    )
    buf = io.StringIO()
    ev.pretty_print(file=buf)
    text = buf.getvalue()
    assert "tool_calls (1)" in text
    assert '"search"' in text


def test_pretty_print_renders_error_section():
    ev = LLMEvent(
        model="gpt-4o",
        provider="openai",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=0.0,
        latency_ms=10,
        error_kind=ErrorKind.RATE_LIMIT,
        error_message="too many requests",
    )
    buf = io.StringIO()
    ev.pretty_print(file=buf)
    text = buf.getvalue()
    assert "[ERROR (rate_limit)]" in text
    assert "kind:    rate_limit" in text
    assert "too many requests" in text


# ----------------------------------------------------------------------
# ReplayResult.summary / pretty_print
# ----------------------------------------------------------------------


def test_replay_result_summary_identical_format():
    result = ReplayResult(
        original_request_id="orig-1",
        new_request_id="new-1",
        text_identical=True,
        tokens_delta=0,
        latency_ms_delta=10,
    )
    s = result.summary()
    assert "orig-1" in s and "new-1" in s
    assert "identical" in s
    assert "tokens +0" in s
    assert "latency +10ms" in s


def test_replay_result_summary_different_format():
    result = ReplayResult(
        original_request_id="orig-1",
        new_request_id="new-1",
        text_identical=False,
        tokens_delta=-5,
        latency_ms_delta=-200,
    )
    s = result.summary()
    assert "different" in s
    assert "tokens -5" in s
    assert "latency -200ms" in s


def test_replay_result_summary_error_branch():
    result = ReplayResult(original_request_id="orig-1", error_message="boom")
    assert "ERROR" in result.summary()
    assert "boom" in result.summary()


def test_replay_result_pretty_print_includes_diff():
    diff = "@@ -1,1 +1,1 @@\n-old\n+new\n"
    result = ReplayResult(
        original_request_id="orig-1",
        new_request_id="new-1",
        text_identical=False,
        text_diff=diff,
        tokens_delta=2,
        latency_ms_delta=10,
    )
    buf = io.StringIO()
    result.pretty_print(file=buf)
    text = buf.getvalue()
    assert "Replay orig-1" in text
    assert "── diff ──" in text
    assert "-old" in text and "+new" in text


def test_replay_result_pretty_print_error_short_circuits():
    result = ReplayResult(original_request_id="orig-1", error_message="boom")
    buf = io.StringIO()
    result.pretty_print(file=buf)
    text = buf.getvalue()
    assert "ERROR" in text
    assert "boom" in text
    # No tokens/latency lines on error branch
    assert "tokens:" not in text


# ----------------------------------------------------------------------
# last_event / recent_events ring buffer
# ----------------------------------------------------------------------


def _client(*, config_overrides=None) -> LeanLLM:
    base = dict(enable_persistence=False)
    if config_overrides:
        base.update(config_overrides)
    return LeanLLM(api_key="sk-test", config=LeanLLMConfig(**base))


def test_last_event_starts_none():
    client = _client()
    assert client.last_event is None


def test_last_event_returns_most_recent_after_chat(monkeypatch):
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="hi")
    )
    client = _client()
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    ev = client.last_event
    assert ev is not None
    assert ev.model == "gpt-4o-mini"


def test_recent_events_returns_tail_in_order(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client()
    for i in range(3):
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id=f"req-{i}",
        )
    recent = client.recent_events(n=2)
    assert [e.event_id for e in recent] == ["req-1", "req-2"]


def test_recent_events_n_zero_returns_empty(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client()
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert client.recent_events(n=0) == []


def test_buffer_size_zero_disables_recent_events(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"last_event_buffer": 0})
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert client.last_event is None
    assert client.recent_events() == []


def test_buffer_evicts_oldest_when_full(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"last_event_buffer": 2})
    for i in range(4):
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id=f"req-{i}",
        )
    recent = client.recent_events(n=10)
    # only the last two survive
    assert [e.event_id for e in recent] == ["req-2", "req-3"]


def test_recent_events_works_when_persistence_disabled(monkeypatch):
    """The buffer is process memory; it works independently of the worker/store."""
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client()  # enable_persistence=False
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert client.last_event is not None


# ----------------------------------------------------------------------
# leanllm.init / leanllm.chat / leanllm.completion / shutdown
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with no default client."""
    leanllm.shutdown()
    yield
    leanllm.shutdown()


def test_init_returns_singleton_on_repeat_calls():
    c1 = leanllm.init(api_key="sk-a", config=LeanLLMConfig(enable_persistence=False))
    c2 = leanllm.init(api_key="sk-b", config=LeanLLMConfig(enable_persistence=False))
    assert c1 is c2


def test_get_default_client_none_until_init():
    assert leanllm.get_default_client() is None
    client = leanllm.init(api_key="sk", config=LeanLLMConfig(enable_persistence=False))
    assert leanllm.get_default_client() is client


def test_top_level_chat_routes_to_singleton(monkeypatch):
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="hi")
    )
    leanllm.init(api_key="sk", config=LeanLLMConfig(enable_persistence=False))
    response = leanllm.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}]
    )
    assert response is not None


def test_top_level_completion_routes_to_singleton(monkeypatch):
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="ok")
    )
    leanllm.init(api_key="sk", config=LeanLLMConfig(enable_persistence=False))
    response = leanllm.completion(model="gpt-4o-mini", prompt="hi")
    assert response is not None


def test_top_level_chat_raises_without_init():
    with pytest.raises(RuntimeError, match="leanllm.init"):
        leanllm.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])


def test_shutdown_drops_singleton_so_init_can_rebuild():
    c1 = leanllm.init(api_key="sk", config=LeanLLMConfig(enable_persistence=False))
    leanllm.shutdown()
    assert leanllm.get_default_client() is None
    c2 = leanllm.init(api_key="sk", config=LeanLLMConfig(enable_persistence=False))
    assert c2 is not c1


# ----------------------------------------------------------------------
# Auto-chain
# ----------------------------------------------------------------------


def test_auto_chain_off_by_default(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client()
    for i in range(2):
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id=f"r-{i}",
        )
        events.append(client.last_event)
    # second call has no parent because auto_chain is off
    assert events[0].parent_request_id is None
    assert events[1].parent_request_id is None


def test_auto_chain_advances_parent_request_id_across_calls(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"auto_chain": True})
    set_auto_chain_parent(event_id=None)  # reset (test isolation)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-0",
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-1",
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-2",
    )
    recent = client.recent_events(n=3)
    assert recent[0].parent_request_id is None
    assert recent[1].parent_request_id == "r-0"
    assert recent[2].parent_request_id == "r-1"


def test_auto_chain_explicit_parent_kwarg_wins(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"auto_chain": True})
    set_auto_chain_parent(event_id=None)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-0",
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-1",
        parent_request_id="EXPLICIT",
    )
    last = client.last_event
    assert last.parent_request_id == "EXPLICIT"


def test_auto_chain_resets_inside_trace(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"auto_chain": True})
    set_auto_chain_parent(event_id=None)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="outer-1",
    )
    with trace(correlation_id="C-inner"):
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id="inner-1",
        )
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id="inner-2",
        )
    recent = client.recent_events(n=3)
    by_id = {e.event_id: e for e in recent}
    # inner-1 starts a fresh chain (no parent) because trace() reset the var
    assert by_id["inner-1"].parent_request_id is None
    # inner-2 inherits inner-1
    assert by_id["inner-2"].parent_request_id == "inner-1"


def test_auto_chain_disabled_via_config(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"auto_chain": False})
    set_auto_chain_parent(event_id="should-not-be-used")
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="r-0",
    )
    assert client.last_event.parent_request_id is None


def test_auto_chain_async_tasks_inherit_via_contextvar(monkeypatch):
    """asyncio tasks spawned from the same parent inherit the auto-chain pointer."""
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(config_overrides={"auto_chain": True})

    async def runner():
        set_auto_chain_parent(event_id=None)
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            request_id="root",
        )
        # Tasks spawned now inherit the auto-chain var
        seen: List[str] = []

        async def child(label):
            client.chat(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "x"}],
                request_id=f"child-{label}",
            )
            seen.append(client.last_event.parent_request_id or "")
            return seen

        await child("a")
        return seen

    result = asyncio.run(runner())
    assert result == ["root"]


# ----------------------------------------------------------------------
# Auto-chain ContextVar helpers
# ----------------------------------------------------------------------


def test_get_auto_chain_parent_default_is_none():
    set_auto_chain_parent(event_id=None)
    assert get_auto_chain_parent() is None


def test_set_and_get_auto_chain_parent_round_trip():
    set_auto_chain_parent(event_id="evt-x")
    assert get_auto_chain_parent() == "evt-x"
    set_auto_chain_parent(event_id=None)
    assert get_auto_chain_parent() is None
