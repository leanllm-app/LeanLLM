from __future__ import annotations

from typing import Any, Dict, List

import pytest

import leanllm
from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.client import _CAPTURED_PARAMETERS, _classify_error, _tool_call_to_dict
from leanllm.events.models import ErrorKind
from tests.conftest import make_chunk, make_response


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def test_classify_error_timeout():
    class TimeoutError(Exception):
        pass

    assert _classify_error(TimeoutError("x")) == ErrorKind.TIMEOUT


def test_classify_error_rate_limit():
    class RateLimitError(Exception):
        pass

    assert _classify_error(RateLimitError("x")) == ErrorKind.RATE_LIMIT


def test_classify_error_parsing():
    class JSONDecodeError(Exception):
        pass

    assert _classify_error(JSONDecodeError("x")) == ErrorKind.PARSING_ERROR


def test_classify_error_provider():
    class APIConnectionError(Exception):
        pass

    assert _classify_error(APIConnectionError("x")) == ErrorKind.PROVIDER_ERROR


def test_classify_error_unknown_default():
    class WeirdError(Exception):
        pass

    assert _classify_error(WeirdError("x")) == ErrorKind.UNKNOWN


def test_tool_call_to_dict_passes_dict_through():
    raw = {"id": "call_1", "function": {"name": "search", "arguments": "{}"}}
    assert _tool_call_to_dict(raw) is raw


def test_tool_call_to_dict_uses_model_dump_when_present():
    class FakeToolCall:
        def model_dump(self):
            return {"name": "x"}

    assert _tool_call_to_dict(FakeToolCall()) == {"name": "x"}


def test_tool_call_to_dict_falls_back_to_repr():
    class Bare:
        def __repr__(self):
            return "Bare()"

    assert _tool_call_to_dict(Bare()) == {"raw": "Bare()"}


# ----------------------------------------------------------------------
# Client construction / no-op surface
# ----------------------------------------------------------------------


def _make_noop_client(**kwargs) -> LeanLLM:
    config = LeanLLMConfig(enable_persistence=False)
    return LeanLLM(api_key="sk-test", config=config, **kwargs)


def test_client_with_persistence_disabled_does_not_start_worker():
    client = _make_noop_client()
    assert client._queue is None
    assert client._worker is None


def test_client_without_url_or_key_logs_info_and_skips_worker(caplog):
    config = LeanLLMConfig(enable_persistence=True)
    with caplog.at_level("INFO"):
        client = LeanLLM(api_key="sk-test", config=config)
    assert client._queue is None
    assert client._worker is None
    assert any("events will not be persisted" in m for m in caplog.messages)


def test_public_import_surface_exposes_core_symbols():
    for name in ("LeanLLM", "LeanLLMConfig", "LLMEvent"):
        assert hasattr(leanllm, name), f"missing public export: {name}"


# ----------------------------------------------------------------------
# Non-streaming chat — return value, hooks, identifiers
# ----------------------------------------------------------------------


def test_chat_returns_raw_litellm_response_unchanged(monkeypatch):
    response = make_response(content="hi")
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)
    client = _make_noop_client()
    result = client.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "yo"}]
    )
    assert result is response


def test_pre_call_hook_receives_snapshot_with_ids_and_payload(monkeypatch):
    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(),
    )
    client = _make_noop_client(pre_call_hook=captured.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        request_id="req-1",
    )
    assert len(captured) == 1
    snap = captured[0]
    assert snap["request_id"] == "req-1"
    assert snap["model"] == "gpt-4o-mini"
    assert snap["messages"] == [{"role": "user", "content": "hi"}]


def test_post_call_hook_fires_on_success(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content="hi"),
    )
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "yo"}])
    assert len(events) == 1
    assert events[0].model == "gpt-4o-mini"


def test_post_call_hook_does_not_fire_on_error(monkeypatch):
    events: List[LLMEvent] = []

    def boom(**kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr("leanllm.client.chat_completion", boom)
    client = _make_noop_client(post_call_hook=events.append)
    with pytest.raises(RuntimeError):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "yo"}])
    assert events == []


def test_error_hook_fires_on_exception(monkeypatch):
    seen: List[tuple] = []

    def boom(**kw):
        raise RuntimeError("oops")

    monkeypatch.setattr("leanllm.client.chat_completion", boom)
    client = _make_noop_client(error_hook=lambda exc, snap: seen.append((exc, snap)))
    with pytest.raises(RuntimeError):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert len(seen) == 1
    assert isinstance(seen[0][0], RuntimeError)
    assert seen[0][1]["model"] == "gpt-4o-mini"


def test_request_id_override_becomes_event_id(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        request_id="req-zzz",
    )
    assert events[0].event_id == "req-zzz"


def test_correlation_id_kwarg_is_persisted(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        correlation_id="corr-1",
    )
    assert events[0].correlation_id == "corr-1"


def test_parent_request_id_kwarg_is_persisted(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        parent_request_id="root-1",
    )
    assert events[0].parent_request_id == "root-1"


def test_parameters_whitelist_captured_and_unknown_dropped(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        temperature=0.7,
        max_tokens=20,
        stream=False,
        super_secret_kwarg="dropped",
    )
    params = events[0].parameters
    assert params == {"temperature": 0.7, "max_tokens": 20, "stream": False}
    assert "super_secret_kwarg" not in params
    assert "super_secret_kwarg" not in _CAPTURED_PARAMETERS


def test_tools_kwarg_captured_on_event(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    tools = [{"type": "function", "function": {"name": "search"}}]
    client.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}], tools=tools
    )
    assert events[0].tools == tools


def test_tools_falls_back_to_functions_kwarg(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)
    functions = [{"name": "old_style"}]
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        functions=functions,
    )
    assert events[0].tools == functions


def test_tool_calls_captured_from_response(monkeypatch):
    class FakeTC:
        def model_dump(self):
            return {"id": "call_1", "function": {"name": "lookup", "arguments": "{}"}}

    response = make_response(content=None, tool_calls=[FakeTC()])
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)
    events: List[LLMEvent] = []
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert events[0].tool_calls == [
        {"id": "call_1", "function": {"name": "lookup", "arguments": "{}"}}
    ]


# ----------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------


def test_streaming_yields_all_chunks(monkeypatch):
    chunks = [make_chunk(delta_content=t) for t in ("a", "b", "c")]
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: iter(chunks))
    client = _make_noop_client()
    out = list(
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            stream=True,
        )
    )
    assert out == chunks


def test_streaming_records_ttft_and_total_time(monkeypatch):
    events: List[LLMEvent] = []
    chunks = [
        make_chunk(delta_content="hel"),
        make_chunk(delta_content="lo", finish_reason="stop"),
    ]
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: iter(chunks))
    client = _make_noop_client(post_call_hook=events.append)
    list(
        client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            stream=True,
        )
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.metadata.get("stream") is True
    assert ev.time_to_first_token_ms is not None
    assert ev.total_stream_time_ms is not None


def test_streaming_error_emits_error_event_and_reraises(monkeypatch):
    events: List[LLMEvent] = []
    error_log: List[Exception] = []

    def gen():
        yield make_chunk(delta_content="ok")
        raise RuntimeError("stream broke")

    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: gen())
    client = _make_noop_client(
        post_call_hook=events.append,
        error_hook=lambda exc, snap: error_log.append(exc),
    )
    with pytest.raises(RuntimeError):
        for _ in client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            stream=True,
        ):
            pass
    assert error_log and isinstance(error_log[0], RuntimeError)
    # post_call_hook does NOT fire on error path
    assert events == []


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


def test_response_with_no_choices_leaves_content_none(monkeypatch):
    events: List[LLMEvent] = []
    response = make_response(no_choices=True)
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    ev = events[0]
    assert ev.metadata.get("finish_reason") is None
    assert ev.tool_calls is None


def test_missing_provider_usage_triggers_token_estimate(monkeypatch):
    events: List[LLMEvent] = []
    response = make_response(content="hello world", no_usage=True)
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: response)
    client = _make_noop_client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "give me a hello"}]
    )
    ev = events[0]
    assert ev.input_tokens > 0
    assert ev.output_tokens > 0


def test_pre_call_hook_raising_does_not_break_request(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())

    def angry_hook(snap):
        raise ValueError("hook says no")

    client = _make_noop_client(pre_call_hook=angry_hook)
    result = client.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}]
    )
    assert result is not None


def test_post_call_hook_raising_does_not_break_request(monkeypatch):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())

    def angry_hook(event):
        raise ValueError("hook says no")

    client = _make_noop_client(post_call_hook=angry_hook)
    result = client.chat(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}]
    )
    assert result is not None


def test_error_hook_raising_does_not_swallow_original(monkeypatch):
    def boom(**kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr("leanllm.client.chat_completion", boom)

    def evil_hook(exc, snap):
        raise ValueError("hook itself fails")

    client = _make_noop_client(error_hook=evil_hook)
    with pytest.raises(RuntimeError, match="provider down"):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])


def test_chat_reraises_underlying_exception(monkeypatch):
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    events: List[LLMEvent] = []
    client = _make_noop_client(post_call_hook=events.append)
    with pytest.raises(RuntimeError, match="boom"):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
