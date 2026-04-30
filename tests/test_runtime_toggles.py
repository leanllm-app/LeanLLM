"""Module 14 — per-request toggles + producer-side sampling + environment + debug."""

from __future__ import annotations

import logging
import random
from typing import List

import pytest

from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.client import _resolve_environment, _should_sample
from leanllm.context import LeanLLMContext, set_global_context
from leanllm.events.models import ErrorKind
from leanllm.redaction import RedactionMode
from tests.conftest import make_response


def _client(*, config_overrides=None, **client_kwargs) -> LeanLLM:
    base = dict(enable_persistence=False)
    if config_overrides:
        base.update(config_overrides)
    return LeanLLM(api_key="sk-test", config=LeanLLMConfig(**base), **client_kwargs)


# ----------------------------------------------------------------------
# _should_sample helper (pure)
# ----------------------------------------------------------------------


def test_should_sample_rate_one_always_keeps():
    assert _should_sample(rate=1.0) is True
    assert _should_sample(rate=1.5) is True


def test_should_sample_rate_zero_always_drops():
    assert _should_sample(rate=0.0) is False
    assert _should_sample(rate=-0.1) is False


def test_should_sample_partial_uses_random(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.49)
    assert _should_sample(rate=0.5) is True
    monkeypatch.setattr(random, "random", lambda: 0.51)
    assert _should_sample(rate=0.5) is False


# ----------------------------------------------------------------------
# _resolve_environment helper (pure)
# ----------------------------------------------------------------------


def test_resolve_environment_context_wins_over_config():
    config = LeanLLMConfig(enable_persistence=False, environment="prod")
    context = LeanLLMContext(environment="staging")
    assert _resolve_environment(config=config, context=context) == "staging"


def test_resolve_environment_falls_back_to_config_when_context_none_field():
    config = LeanLLMConfig(enable_persistence=False, environment="prod")
    context = LeanLLMContext(user_id="u1")  # environment=None
    assert _resolve_environment(config=config, context=context) == "prod"


def test_resolve_environment_returns_none_when_neither_set():
    config = LeanLLMConfig(enable_persistence=False)
    assert _resolve_environment(config=config, context=None) is None


# ----------------------------------------------------------------------
# Config + from_env wiring
# ----------------------------------------------------------------------


def test_config_defaults_for_module_14_fields():
    cfg = LeanLLMConfig()
    assert cfg.sampling_rate == 1.0
    assert cfg.environment is None
    assert cfg.debug is False


def test_from_env_reads_sampling_rate(monkeypatch):
    for v in ("LEANLLM_API_KEY", "LEANLLM_DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    monkeypatch.setenv("LEANLLM_SAMPLING_RATE", "0.25")
    cfg = LeanLLMConfig.from_env()
    assert cfg.sampling_rate == 0.25


def test_from_env_reads_environment(monkeypatch):
    for v in ("LEANLLM_API_KEY", "LEANLLM_DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    monkeypatch.setenv("LEANLLM_ENVIRONMENT", "production")
    cfg = LeanLLMConfig.from_env()
    assert cfg.environment == "production"


def test_from_env_reads_debug(monkeypatch):
    for v in ("LEANLLM_API_KEY", "LEANLLM_DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    monkeypatch.setenv("LEANLLM_DEBUG", "true")
    cfg = LeanLLMConfig.from_env()
    assert cfg.debug is True


# ----------------------------------------------------------------------
# log=False — full bypass
# ----------------------------------------------------------------------


def test_log_false_bypasses_all_hooks_and_event_emission(monkeypatch):
    pre_called: List = []
    post_called: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        pre_call_hook=pre_called.append,
        post_call_hook=post_called.append,
    )
    response = client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        log=False,
    )
    assert response is not None
    assert pre_called == []
    assert post_called == []


def test_log_true_default_still_emits(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert len(events) == 1


# ----------------------------------------------------------------------
# Sampling — producer-side, errors bypass
# ----------------------------------------------------------------------


def test_global_sampling_rate_zero_drops_success_events(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        config_overrides={"sampling_rate": 0.0},
        post_call_hook=events.append,
    )
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert events == []


def test_global_sampling_rate_zero_still_emits_errors(monkeypatch):
    events: List[LLMEvent] = []
    error_seen: List[Exception] = []

    def boom(**kw):
        raise RuntimeError("provider failed")

    monkeypatch.setattr("leanllm.client.chat_completion", boom)
    # post_call_hook only fires on success; for errors we look at queue/store side.
    # Use a Hook of error_hook to confirm error path runs.
    client = _client(
        config_overrides={"sampling_rate": 0.0},
        post_call_hook=events.append,
        error_hook=lambda exc, snap: error_seen.append(exc),
    )
    with pytest.raises(RuntimeError):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    # post_call_hook never fires on error path (existing behavior)
    assert events == []
    # error_hook DID fire — errors bypass sampling
    assert len(error_seen) == 1


def test_per_call_sample_override_zero_drops_event(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        sample=0.0,
    )
    assert events == []


def test_per_call_sample_override_one_keeps_event_even_when_global_is_zero(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        config_overrides={"sampling_rate": 0.0},
        post_call_hook=events.append,
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        sample=1.0,
    )
    assert len(events) == 1


def test_pre_call_hook_fires_even_when_sampled_out(monkeypatch):
    """Sampling controls persistence, not observability — pre_call still fires."""
    pre_called: List = []
    post_called: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        config_overrides={"sampling_rate": 0.0},
        pre_call_hook=pre_called.append,
        post_call_hook=post_called.append,
    )
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert len(pre_called) == 1
    assert post_called == []


# ----------------------------------------------------------------------
# Per-call redaction_mode override
# ----------------------------------------------------------------------


def test_per_call_redaction_mode_override_full_overrides_default_metadata(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content="email: agent@spy.com"),
    )
    # config defaults to METADATA_ONLY (would yield prompt/response = None)
    client = _client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        redaction_mode=RedactionMode.FULL,
    )
    ev = events[0]
    assert ev.response == "email: agent@spy.com"
    assert ev.prompt is not None  # FULL preserves the prompt JSON


def test_per_call_redaction_mode_override_redacted_masks_pii(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content="ping me at user@example.com"),
    )
    client = _client(post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        redaction_mode=RedactionMode.REDACTED,
    )
    ev = events[0]
    assert "[EMAIL]" in (ev.response or "")


# ----------------------------------------------------------------------
# Environment field — flows into LLMEvent.metadata
# ----------------------------------------------------------------------


def test_config_environment_lands_in_event_metadata(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        config_overrides={"environment": "production"},
        post_call_hook=events.append,
    )
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert events[0].metadata.get("environment") == "production"


def test_context_environment_overrides_config_environment(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(
        config_overrides={"environment": "production"},
        post_call_hook=events.append,
    )
    set_global_context(context=LeanLLMContext(environment="staging"))
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert events[0].metadata.get("environment") == "staging"


def test_environment_absent_when_neither_set(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client(post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert "environment" not in events[0].metadata


def test_environment_lands_on_error_event_metadata(monkeypatch):
    def boom(**kw):
        raise RuntimeError("nope")

    # Capture error events via a fake queue to inspect metadata.

    monkeypatch.setattr("leanllm.client.chat_completion", boom)
    client = _client(config_overrides={"environment": "production"})
    # client has _queue=None when persistence disabled; use a side-channel:
    captured: List[LLMEvent] = []

    def spy_emit_error(**kwargs):
        # Build the event manually to inspect
        event = client._build_error_event(**kwargs)
        captured.append(event)

    monkeypatch.setattr(client, "_emit_error", spy_emit_error)
    with pytest.raises(RuntimeError):
        client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert captured[0].metadata.get("environment") == "production"


# ----------------------------------------------------------------------
# Debug mode — DEBUG log level + per-event stderr summary
# ----------------------------------------------------------------------


def test_debug_mode_sets_logger_level_to_debug():
    # Reset to a known state first
    logging.getLogger("leanllm").setLevel(logging.WARNING)
    LeanLLM(
        api_key="sk-test",
        config=LeanLLMConfig(enable_persistence=False, debug=True),
    )
    assert logging.getLogger("leanllm").level == logging.DEBUG


def test_debug_mode_prints_event_summary_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="hi")
    )
    client = _client(config_overrides={"debug": True})
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    captured = capsys.readouterr()
    assert "gpt-4o-mini" in captured.err
    assert "tokens=" in captured.err


def test_debug_mode_off_does_not_print(monkeypatch, capsys):
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _client()
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    captured = capsys.readouterr()
    assert captured.err == ""


# ----------------------------------------------------------------------
# LLMEvent.summary()
# ----------------------------------------------------------------------


def test_event_summary_success_format():
    ev = LLMEvent(
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=5,
        output_tokens=10,
        total_tokens=15,
        cost=0.0023,
        latency_ms=1400,
    )
    s = ev.summary()
    assert "gpt-4o-mini" in s
    assert "tokens=5/10" in s
    assert "cost=$0.0023" in s
    assert "latency=1400ms" in s


def test_event_summary_error_format():
    ev = LLMEvent(
        model="gpt-4o",
        provider="openai",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=0.0,
        latency_ms=10,
        error_kind=ErrorKind.RATE_LIMIT,
        error_message="too many",
    )
    s = ev.summary()
    assert "ERROR(rate_limit)" in s
    assert "too many" in s
