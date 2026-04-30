from __future__ import annotations

from leanllm import LeanLLM, LeanLLMConfig
from leanllm.normalizer import (
    InputType,
    LengthBucket,
    OutputType,
    canonicalize,
    classify_input_type,
    classify_output,
    detect_language,
    length_bucket,
    normalize_input,
    normalize_output,
    semantic_hash,
)
from tests.conftest import make_response


# ----------------------------------------------------------------------
# canonicalize
# ----------------------------------------------------------------------


def test_canonicalize_masks_uuid():
    out = canonicalize(text="see request 550e8400-e29b-41d4-a716-446655440000 ok")
    assert "<uuid>" in out
    assert "550e8400" not in out


def test_canonicalize_masks_iso_timestamp():
    out = canonicalize(text="At 2026-04-27T10:00:00Z something happened")
    assert "<ts>" in out
    assert "2026-04-27" not in out


def test_canonicalize_masks_long_hex_id():
    out = canonicalize(text="hash deadbeefcafebabe1234 is bad")
    assert "<hex>" in out


def test_canonicalize_masks_long_numbers():
    out = canonicalize(text="order 1234567 is processed")
    assert "<num>" in out
    assert "1234567" not in out


def test_canonicalize_strips_lowers_and_collapses_whitespace():
    out = canonicalize(text="   HELLO    World\n\n  ")
    assert out == "hello world"


def test_canonicalize_is_idempotent():
    text = "User 1234567 placed order at 2026-04-27T10:00:00Z"
    once = canonicalize(text=text)
    twice = canonicalize(text=once)
    assert once == twice


# ----------------------------------------------------------------------
# semantic_hash
# ----------------------------------------------------------------------


def test_semantic_hash_is_16_hex_chars():
    h = semantic_hash(text="hello")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_semantic_hash_groups_calls_with_dynamic_tokens_swapped():
    a = semantic_hash(text="user 1234567 logged in at 2026-04-27T10:00:00Z")
    b = semantic_hash(text="user 9876543 logged in at 2026-04-27T11:00:00Z")
    assert a == b


def test_semantic_hash_collides_when_only_casing_or_whitespace_differs():
    a = semantic_hash(text="Hello   World")
    b = semantic_hash(text="hello world")
    assert a == b


def test_semantic_hash_differs_for_semantically_different_inputs():
    assert semantic_hash(text="hello world") != semantic_hash(text="goodbye world")


# ----------------------------------------------------------------------
# length_bucket
# ----------------------------------------------------------------------


def test_length_bucket_short_for_empty_string():
    assert length_bucket(text="") == LengthBucket.SHORT


def test_length_bucket_short_at_boundary_50_words():
    text = " ".join(["w"] * 50)
    assert length_bucket(text=text) == LengthBucket.SHORT


def test_length_bucket_medium_above_50_words():
    text = " ".join(["w"] * 100)
    assert length_bucket(text=text) == LengthBucket.MEDIUM


def test_length_bucket_long_above_500_words():
    text = " ".join(["w"] * 600)
    assert length_bucket(text=text) == LengthBucket.LONG


# ----------------------------------------------------------------------
# detect_language
# ----------------------------------------------------------------------


def test_detect_language_latin():
    assert detect_language(text="hello world") == "latin"


def test_detect_language_cjk():
    assert detect_language(text="你好世界") == "cjk"


def test_detect_language_cyrillic():
    assert detect_language(text="Привет мир") == "cyrillic"


def test_detect_language_arabic():
    assert detect_language(text="مرحبا بالعالم") == "arabic"


def test_detect_language_returns_none_for_digits_only():
    assert detect_language(text="123 456") is None


def test_detect_language_returns_none_for_whitespace():
    assert detect_language(text="   ") is None


# ----------------------------------------------------------------------
# classify_output
# ----------------------------------------------------------------------


def test_classify_output_json_object():
    t, structure = classify_output(text='{"a": 1}')
    assert t == OutputType.JSON
    assert structure == "json"


def test_classify_output_json_array():
    t, structure = classify_output(text="[1, 2, 3]")
    assert t == OutputType.JSON


def test_classify_output_code_fence():
    t, structure = classify_output(text="here:\n```py\nprint('x')\n```")
    assert t == OutputType.CODE
    assert structure == "fenced_code"


def test_classify_output_text_default():
    t, structure = classify_output(text="just a sentence.")
    assert t == OutputType.TEXT
    assert structure is None


def test_classify_output_invalid_json_falls_to_text():
    t, _ = classify_output(text='{"missing": ')
    assert t == OutputType.TEXT


# ----------------------------------------------------------------------
# classify_input_type
# ----------------------------------------------------------------------


def test_classify_input_type_chat():
    assert (
        classify_input_type(messages=[{"role": "user", "content": "hi"}])
        == InputType.CHAT
    )


def test_classify_input_type_tool():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "result"},
    ]
    assert classify_input_type(messages=msgs) == InputType.TOOL


def test_classify_input_type_unknown_for_empty_messages():
    assert classify_input_type(messages=[]) == InputType.UNKNOWN


# ----------------------------------------------------------------------
# normalize_input / normalize_output
# ----------------------------------------------------------------------


def test_normalize_input_no_auto_tag_yields_unknown_input_type():
    result = normalize_input(messages=[{"role": "user", "content": "hello"}])
    assert result.input_type == InputType.UNKNOWN
    assert result.language is None
    assert result.length_bucket == LengthBucket.SHORT
    assert result.semantic_hash is not None


def test_normalize_input_auto_tag_fills_input_type_and_language():
    result = normalize_input(
        messages=[{"role": "user", "content": "hello world this is plain English"}],
        auto_tag=True,
    )
    assert result.input_type == InputType.CHAT
    assert result.language == "latin"


def test_normalize_input_intent_always_none():
    result = normalize_input(
        messages=[{"role": "user", "content": "hi"}],
        auto_tag=True,
    )
    assert result.intent is None


def test_normalize_input_empty_messages_returns_short_no_hash():
    result = normalize_input(messages=[])
    assert result.length_bucket == LengthBucket.SHORT
    assert result.semantic_hash is None
    assert result.input_type == InputType.UNKNOWN


def test_normalize_input_skips_messages_without_content():
    msgs = [
        {"role": "user"},
        {"role": "user", "content": None},
        {"role": "user", "content": "x"},
    ]
    result = normalize_input(messages=msgs, auto_tag=True)
    assert result.semantic_hash is not None


def test_normalize_output_no_auto_tag_keeps_unknown_type():
    result = normalize_output(text="hello")
    assert result.output_type == OutputType.UNKNOWN
    assert result.length_bucket == LengthBucket.SHORT
    assert result.structure_detected is None


def test_normalize_output_auto_tag_classifies_json():
    result = normalize_output(text='{"a": 1}', auto_tag=True)
    assert result.output_type == OutputType.JSON
    assert result.structure_detected == "json"


# ----------------------------------------------------------------------
# Client integration — auto_normalize=True wires fields onto LLMEvent
# ----------------------------------------------------------------------


def test_client_auto_normalize_populates_normalized_input_and_output(monkeypatch):
    events = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content='{"answer": 42}'),
    )
    config = LeanLLMConfig(enable_persistence=False, auto_normalize=True)
    client = LeanLLM(api_key="sk-test", config=config, post_call_hook=events.append)
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "what is the answer"}],
    )
    ev = events[0]
    assert ev.normalized_input is not None
    assert ev.normalized_input.input_type == InputType.CHAT
    assert ev.normalized_output is not None
    assert ev.normalized_output.output_type == OutputType.JSON


def test_client_auto_normalize_off_leaves_fields_none(monkeypatch):
    events = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content="hi"),
    )
    config = LeanLLMConfig(enable_persistence=False, auto_normalize=False)
    client = LeanLLM(api_key="sk-test", config=config, post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    ev = events[0]
    assert ev.normalized_input is None
    assert ev.normalized_output is None


def test_client_auto_normalize_tool_call_branch(monkeypatch):
    class FakeTC:
        def model_dump(self):
            return {"id": "1", "function": {"name": "lookup", "arguments": "{}"}}

    events = []
    monkeypatch.setattr(
        "leanllm.client.chat_completion",
        lambda **kw: make_response(content=None, tool_calls=[FakeTC()]),
    )
    config = LeanLLMConfig(enable_persistence=False, auto_normalize=True)
    client = LeanLLM(api_key="sk-test", config=config, post_call_hook=events.append)
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    ev = events[0]
    assert ev.normalized_output is not None
    assert ev.normalized_output.output_type == OutputType.TOOL_CALL
