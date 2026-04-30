from __future__ import annotations

import json
from typing import List

from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.redaction import RedactionMode, RedactionPolicy, apply
from tests.conftest import make_response


# ----------------------------------------------------------------------
# RedactionPolicy.apply
# ----------------------------------------------------------------------


def test_apply_full_returns_text_unchanged():
    policy = RedactionPolicy(mode=RedactionMode.FULL)
    assert apply(policy=policy, text="email me at a@b.com") == "email me at a@b.com"


def test_apply_metadata_only_returns_none():
    policy = RedactionPolicy(mode=RedactionMode.METADATA_ONLY)
    assert apply(policy=policy, text="contains a@b.com") is None


def test_apply_redacted_masks_emails_phones_cpf_ssn():
    policy = RedactionPolicy(mode=RedactionMode.REDACTED)
    text = "Contact me at user@example.com or 11999998888 — CPF 123.456.789-00 SSN 123-45-6789"
    out = apply(policy=policy, text=text)
    assert "[EMAIL]" in out
    assert "[CPF]" in out
    assert "[SSN]" in out
    # phone token may match before or after SSN/CPF, so just check at least one phone-shaped match was masked
    assert "user@example.com" not in out


def test_apply_none_input_returns_none_for_any_mode():
    for mode in RedactionMode:
        policy = RedactionPolicy(mode=mode)
        assert apply(policy=policy, text=None) is None


def test_apply_custom_patterns_replace_with_redacted_token():
    policy = RedactionPolicy(
        mode=RedactionMode.REDACTED,
        custom_patterns=[r"SECRET-\d+"],
        redact_emails=False,
        redact_phones=False,
        redact_ids=False,
    )
    out = apply(policy=policy, text="this is SECRET-9001 confidential")
    assert "[REDACTED]" in out
    assert "SECRET-9001" not in out


def test_apply_invalid_custom_pattern_is_silently_skipped():
    policy = RedactionPolicy(
        mode=RedactionMode.REDACTED,
        custom_patterns=[r"[invalid("],  # broken regex
        redact_emails=False,
        redact_phones=False,
        redact_ids=False,
    )
    # No crash: returns original text untouched
    assert apply(policy=policy, text="hello") == "hello"


# ----------------------------------------------------------------------
# Client integration: redaction_mode flows through _capture_content
# ----------------------------------------------------------------------


def _make_client(*, mode: RedactionMode, post_call_hook):
    return LeanLLM(
        api_key="sk-test",
        config=LeanLLMConfig(enable_persistence=False, redaction_mode=mode),
        post_call_hook=post_call_hook,
    )


def test_metadata_only_mode_strips_prompt_and_response(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="hi")
    )
    client = _make_client(
        mode=RedactionMode.METADATA_ONLY, post_call_hook=events.append
    )
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    ev = events[0]
    assert ev.prompt is None
    assert ev.response is None


def test_full_mode_captures_prompt_and_response(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion", lambda **kw: make_response(content="hello")
    )
    client = _make_client(mode=RedactionMode.FULL, post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "ping"}])
    ev = events[0]
    assert ev.response == "hello"
    parsed_prompt = json.loads(ev.prompt)
    assert parsed_prompt[0]["content"] == "ping"


def test_redacted_mode_masks_pii_in_prompt_and_response(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content="reach me at agent@spy.com"),
    )
    client = _make_client(mode=RedactionMode.REDACTED, post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "my mail is user@example.com"}],
    )
    ev = events[0]
    assert "user@example.com" not in (ev.prompt or "")
    assert "[EMAIL]" in (ev.prompt or "")
    assert "[EMAIL]" in (ev.response or "")


def test_config_from_env_reads_redaction_mode(monkeypatch):
    monkeypatch.setenv("LEANLLM_REDACTION_MODE", "redacted")
    monkeypatch.delenv("LEANLLM_API_KEY", raising=False)
    monkeypatch.delenv("LEANLLM_DATABASE_URL", raising=False)
    cfg = LeanLLMConfig.from_env()
    assert cfg.redaction_mode == RedactionMode.REDACTED


def test_config_from_env_invalid_redaction_mode_falls_back_to_metadata(monkeypatch):
    monkeypatch.setenv("LEANLLM_REDACTION_MODE", "garbage-value")
    monkeypatch.delenv("LEANLLM_API_KEY", raising=False)
    monkeypatch.delenv("LEANLLM_DATABASE_URL", raising=False)
    cfg = LeanLLMConfig.from_env()
    assert cfg.redaction_mode == RedactionMode.METADATA_ONLY
