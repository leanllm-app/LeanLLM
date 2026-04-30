from __future__ import annotations

import re
from datetime import datetime, timezone

from leanllm import LLMEvent
from leanllm.events.models import ErrorKind


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _minimal_event(**overrides) -> LLMEvent:
    base = dict(
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=0.0,
        latency_ms=0,
    )
    base.update(overrides)
    return LLMEvent(**base)


def test_default_event_id_is_uuid_shaped():
    ev = _minimal_event()
    assert _UUID_RE.match(ev.event_id) is not None


def test_default_timestamp_is_timezone_aware_utc():
    ev = _minimal_event()
    assert ev.timestamp.tzinfo is not None
    assert ev.timestamp.tzinfo.utcoffset(ev.timestamp).total_seconds() == 0


def test_default_schema_version_is_2():
    ev = _minimal_event()
    assert ev.schema_version == 2


def test_model_dump_serializes_timestamp_as_iso8601():
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev = _minimal_event(timestamp=ts)
    dumped = ev.model_dump(mode="json")
    assert dumped["timestamp"].startswith("2026-01-01T12:00:00")


def test_optional_fields_default_to_none():
    ev = _minimal_event()
    for field in (
        "prompt",
        "response",
        "tools",
        "tool_calls",
        "error_kind",
        "error_message",
        "time_to_first_token_ms",
        "total_stream_time_ms",
        "correlation_id",
        "parent_request_id",
        "normalized_input",
        "normalized_output",
    ):
        assert getattr(ev, field) is None, f"{field} should default to None"


def test_labels_and_metadata_default_to_empty_dict():
    ev = _minimal_event()
    assert ev.labels == {}
    assert ev.metadata == {}
    assert ev.parameters == {}


def test_error_kind_enum_exposes_required_values():
    expected = {"timeout", "rate_limit", "provider_error", "parsing_error", "unknown"}
    assert {e.value for e in ErrorKind} == expected


def test_error_kind_serializes_as_string_value():
    ev = _minimal_event(error_kind=ErrorKind.RATE_LIMIT, error_message="too many")
    dumped = ev.model_dump(mode="json")
    assert dumped["error_kind"] == "rate_limit"
    assert dumped["error_message"] == "too many"
