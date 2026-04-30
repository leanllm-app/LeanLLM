"""Row → LLMEvent hydration shared by SQL backends.

Inverse of `_to_row` in `sqlite.py` / `postgres.py`. The two backends differ in
two places only:

  - SQLite stores JSON as TEXT — values come back as strings that need
    `json.loads`. The timestamp column is also TEXT (ISO-8601 string).
  - Postgres stores JSON as JSONB — asyncpg already decodes those into
    Python dicts/lists. The timestamp comes back as a `datetime`.

The helper accepts a normalized mapping (`Dict[str, Any]`) where the caller has
already done the per-backend coercion. Each backend builds that dict once via
`row_dict_*` and then calls `row_to_event`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from ..events.models import ErrorKind, LLMEvent
from ..normalizer import NormalizedInput, NormalizedOutput


# Column ordering — also used by SQLite to map positional tuple rows to a dict.
_FIELD_NAMES: tuple[str, ...] = (
    "event_id",
    "timestamp",
    "model",
    "provider",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost",
    "latency_ms",
    "labels",
    "prompt",
    "response",
    "metadata",
    "schema_version",
    "correlation_id",
    "parent_request_id",
    "parameters",
    "tools",
    "tool_calls",
    "time_to_first_token_ms",
    "total_stream_time_ms",
    "error_kind",
    "error_message",
    "normalized_input",
    "normalized_output",
)


def field_names() -> tuple[str, ...]:
    return _FIELD_NAMES


def _maybe_load_json(value: Any) -> Any:
    """Normalize a JSON-bearing column to a Python value.

    Accepts: None, str (SQLite TEXT), dict/list (Postgres JSONB already decoded).
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value:
            return None
        return json.loads(value)
    return value


def _coerce_timestamp(value: Any) -> datetime:
    """Accept either a datetime (Postgres) or an ISO string (SQLite)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unexpected timestamp type: {type(value).__name__}")


def row_to_event(row: Mapping[str, Any]) -> LLMEvent:
    """Reconstruct an LLMEvent from a row mapping.

    Raises on validation/parse failure — callers (e.g. `list_events`) are
    expected to log + skip rather than abort the whole query.
    """
    error_kind_raw = row.get("error_kind")
    error_kind: Optional[ErrorKind] = (
        ErrorKind(error_kind_raw) if error_kind_raw else None
    )

    norm_in = _maybe_load_json(row.get("normalized_input"))
    norm_out = _maybe_load_json(row.get("normalized_output"))

    return LLMEvent(
        event_id=row["event_id"],
        timestamp=_coerce_timestamp(row["timestamp"]),
        model=row["model"],
        provider=row["provider"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        total_tokens=row["total_tokens"],
        cost=row["cost"],
        latency_ms=row["latency_ms"],
        labels=_maybe_load_json(row.get("labels")) or {},
        prompt=row.get("prompt"),
        response=row.get("response"),
        metadata=_maybe_load_json(row.get("metadata")) or {},
        schema_version=row.get("schema_version", 2),
        correlation_id=row.get("correlation_id"),
        parent_request_id=row.get("parent_request_id"),
        parameters=_maybe_load_json(row.get("parameters")) or {},
        tools=_maybe_load_json(row.get("tools")),
        tool_calls=_maybe_load_json(row.get("tool_calls")),
        time_to_first_token_ms=row.get("time_to_first_token_ms"),
        total_stream_time_ms=row.get("total_stream_time_ms"),
        error_kind=error_kind,
        error_message=row.get("error_message"),
        normalized_input=NormalizedInput.model_validate(norm_in) if norm_in else None,
        normalized_output=NormalizedOutput.model_validate(norm_out)
        if norm_out
        else None,
    )


def tuple_to_dict(row: tuple) -> Dict[str, Any]:
    """Map a SQLite positional row to a dict using the canonical field order."""
    if len(row) != len(_FIELD_NAMES):
        raise ValueError(f"Row has {len(row)} columns, expected {len(_FIELD_NAMES)}")
    return dict(zip(_FIELD_NAMES, row))
