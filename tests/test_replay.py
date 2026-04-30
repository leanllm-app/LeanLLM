from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from leanllm import (
    LeanLLM,
    LeanLLMConfig,
    LLMEvent,
    ReplayEngine,
    ReplayOverrides,
    ReplayResult,
)
from tests.conftest import make_response


def _client(monkeypatch, *, response=None, side_effect=None) -> LeanLLM:
    response = response if response is not None else make_response(content="new answer")

    if side_effect is not None:
        monkeypatch.setattr("leanllm.client.chat_completion", side_effect)
    else:
        monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)

    return LeanLLM(api_key="sk-test", config=LeanLLMConfig(enable_persistence=False))


def _event(
    *,
    response: str | None = "old answer",
    prompt_messages=None,
    parameters=None,
    tools=None,
    total_tokens: int = 100,
    latency_ms: int = 50,
    model: str = "gpt-4o-mini",
) -> LLMEvent:
    if prompt_messages is None:
        prompt_messages = [{"role": "user", "content": "what?"}]
    return LLMEvent(
        model=model,
        provider="openai",
        input_tokens=10,
        output_tokens=20,
        total_tokens=total_tokens,
        cost=0.001,
        latency_ms=latency_ms,
        prompt=json.dumps(prompt_messages),
        response=response,
        parameters=parameters or {},
        tools=tools,
    )


# ----------------------------------------------------------------------
# replay() — basic flows
# ----------------------------------------------------------------------


def test_replay_runs_through_client_and_returns_result(monkeypatch):
    client = _client(
        monkeypatch,
        response=make_response(
            content="new answer", prompt_tokens=5, completion_tokens=15
        ),
    )
    engine = ReplayEngine(client=client)
    ev = _event(response="old answer", total_tokens=30, latency_ms=80)

    result = engine.replay(event=ev)

    assert isinstance(result, ReplayResult)
    assert result.original_request_id == ev.event_id
    assert result.new_request_id is not None and result.new_request_id != ev.event_id
    assert result.text_before == "old answer"
    assert result.text_after == "new answer"
    assert result.text_identical is False
    assert result.tokens_after == 20
    assert result.tokens_delta == 20 - 30


def test_replay_text_identical_no_diff(monkeypatch):
    client = _client(
        monkeypatch,
        response=make_response(content="same", prompt_tokens=1, completion_tokens=2),
    )
    engine = ReplayEngine(client=client)
    ev = _event(response="same", total_tokens=3)

    result = engine.replay(event=ev)
    assert result.text_identical is True
    assert result.text_diff is None


def test_replay_unified_diff_when_texts_differ(monkeypatch):
    client = _client(monkeypatch, response=make_response(content="hello world\nline 2"))
    engine = ReplayEngine(client=client)
    ev = _event(response="hello mars\nline 2")

    result = engine.replay(event=ev)
    assert result.text_diff is not None
    assert "hello world" in result.text_diff
    assert "hello mars" in result.text_diff


def test_replay_overrides_messages_take_priority(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_chat(**kw):
        captured.update(kw)
        return make_response(content="ok")

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    ev = _event(prompt_messages=[{"role": "user", "content": "ORIGINAL"}])

    overrides = ReplayOverrides(messages=[{"role": "user", "content": "NEW PROMPT"}])
    engine.replay(event=ev, overrides=overrides)
    assert captured["messages"] == [{"role": "user", "content": "NEW PROMPT"}]


def test_replay_overrides_model(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_chat(**kw):
        captured.update(kw)
        return make_response(content="ok")

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    ev = _event(model="gpt-4o-mini")
    engine.replay(event=ev, overrides=ReplayOverrides(model="gpt-4o"))
    assert captured["model"] == "gpt-4o"


def test_replay_overrides_parameters_full_replacement(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_chat(**kw):
        captured.update(kw)
        return make_response(content="ok")

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    ev = _event(parameters={"temperature": 0.7, "max_tokens": 100})
    engine.replay(event=ev, overrides=ReplayOverrides(parameters={"temperature": 0.0}))
    assert captured.get("temperature") == 0.0
    assert "max_tokens" not in captured


def test_replay_strips_stream_param(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_chat(**kw):
        captured.update(kw)
        return make_response(content="ok")

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    ev = _event(parameters={"stream": True, "temperature": 0.5})
    engine.replay(event=ev)
    assert "stream" not in captured
    assert captured.get("temperature") == 0.5


# ----------------------------------------------------------------------
# Error / edge cases
# ----------------------------------------------------------------------


def test_replay_raises_when_prompt_missing_and_no_override(monkeypatch):
    client = _client(monkeypatch)
    engine = ReplayEngine(client=client)
    ev = LLMEvent(
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=0.0,
        latency_ms=0,
    )
    with pytest.raises(ValueError, match="messages were not captured"):
        engine.replay(event=ev)


def test_replay_raises_when_prompt_is_invalid_json(monkeypatch):
    client = _client(monkeypatch)
    engine = ReplayEngine(client=client)
    ev = _event()
    ev.prompt = "not-json"
    with pytest.raises(ValueError, match="not valid JSON"):
        engine.replay(event=ev)


def test_replay_raises_when_prompt_is_not_a_list(monkeypatch):
    client = _client(monkeypatch)
    engine = ReplayEngine(client=client)
    ev = _event()
    ev.prompt = json.dumps({"role": "user", "content": "x"})
    with pytest.raises(ValueError, match="not a messages list"):
        engine.replay(event=ev)


def test_replay_response_with_no_choices_yields_none_text(monkeypatch):
    client = _client(monkeypatch, response=make_response(no_choices=True))
    engine = ReplayEngine(client=client)
    ev = _event(response="something")
    result = engine.replay(event=ev)
    assert result.text_after is None
    assert result.text_diff is None  # one side None, no synthetic diff


def test_replay_overrides_all_none_behaves_like_no_overrides(monkeypatch):
    client = _client(monkeypatch, response=make_response(content="ok"))
    engine = ReplayEngine(client=client)
    ev = _event(parameters={"temperature": 0.5})
    r1 = engine.replay(event=ev, overrides=ReplayOverrides())
    r2 = engine.replay(event=ev, overrides=None)
    assert r1.text_before == r2.text_before
    assert r1.text_after == r2.text_after


# ----------------------------------------------------------------------
# Batch
# ----------------------------------------------------------------------


def test_replay_batch_empty_returns_empty(monkeypatch):
    client = _client(monkeypatch)
    engine = ReplayEngine(client=client)
    assert engine.replay_batch(events=[]) == []


def test_replay_batch_returns_results_in_input_order(monkeypatch):
    responses = ["A", "B", "C"]
    iterator = iter(responses)

    def fake_chat(**kw):
        return make_response(content=next(iterator))

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    events = [_event(response=f"old-{i}") for i in range(3)]
    results = engine.replay_batch(events=events, max_workers=1)
    assert [r.text_after for r in results] == ["A", "B", "C"]


def test_replay_batch_partial_failure_does_not_abort(monkeypatch):
    def fake_chat(**kw):
        return make_response(content="ok")

    client = _client(monkeypatch, side_effect=fake_chat)
    engine = ReplayEngine(client=client)
    bad = _event(response="x")
    bad.prompt = None  # will trigger ValueError
    good = _event(response="y")
    results = engine.replay_batch(events=[good, bad], max_workers=1)
    assert results[0].error_message is None
    assert results[1].error_message is not None
    assert results[1].new_request_id is None
    assert results[1].text_before == "x"
