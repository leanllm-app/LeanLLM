from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, TextIO

from pydantic import BaseModel, Field

from ..normalizer import NormalizedInput, NormalizedOutput


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_event_id() -> str:
    return str(uuid.uuid4())


class Provider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    MISTRAL = "mistral"
    COHERE = "cohere"
    AZURE = "azure"
    BEDROCK = "bedrock"
    VERTEX_AI = "vertex_ai"
    HUGGINGFACE = "huggingface"
    UNKNOWN = "unknown"


class ErrorKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    PROVIDER_ERROR = "provider_error"
    PARSING_ERROR = "parsing_error"
    UNKNOWN = "unknown"


class RequestEvent(BaseModel):
    """Input side of an LLM call — populated before the provider is invoked."""

    request_id: str = Field(default_factory=_new_event_id)
    correlation_id: Optional[str] = None
    parent_request_id: Optional[str] = None

    started_at_utc: datetime = Field(default_factory=_utcnow)

    model: str
    provider: str

    messages: List[Dict[str, Any]] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    tools: Optional[List[Dict[str, Any]]] = None

    labels: Dict[str, str] = Field(default_factory=dict)

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }


class ResponseEvent(BaseModel):
    """Output side of an LLM call — populated after the provider returns (or errors)."""

    request_id: str

    finished_at_utc: datetime = Field(default_factory=_utcnow)

    text: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    latency_ms: int = 0
    time_to_first_token_ms: Optional[int] = None
    total_stream_time_ms: Optional[int] = None

    error_kind: Optional[ErrorKind] = None
    error_message: Optional[str] = None

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }


class LLMEvent(BaseModel):
    """Strongly-typed event emitted after every LLM request."""

    event_id: str = Field(default_factory=_new_event_id)
    correlation_id: Optional[str] = None
    parent_request_id: Optional[str] = None

    timestamp: datetime = Field(default_factory=_utcnow)

    model: str
    provider: str

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float  # USD
    latency_ms: int

    parameters: Dict[str, Any] = Field(default_factory=dict)
    tools: Optional[List[Dict[str, Any]]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    time_to_first_token_ms: Optional[int] = None
    total_stream_time_ms: Optional[int] = None

    error_kind: Optional[ErrorKind] = None
    error_message: Optional[str] = None

    labels: Dict[str, str] = Field(default_factory=dict)

    prompt: Optional[str] = None  # stored only when capture_content=True
    response: Optional[str] = None  # stored only when capture_content=True

    normalized_input: Optional[NormalizedInput] = None  # populated by module 4
    normalized_output: Optional[NormalizedOutput] = None  # populated by module 4

    metadata: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 2

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }

    def summary(self) -> str:
        """One-line, human-readable description of this event.

        Used by debug mode (Module 14) and the CLI (Module 13). See
        `pretty_print()` for the multi-line view.
        """
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        if self.error_kind is not None:
            return (
                f"[{ts}] {self.model} ERROR({self.error_kind.value}): "
                f"{self.error_message or '<no message>'}"
            )
        return (
            f"[{ts}] {self.model} tokens={self.input_tokens}/{self.output_tokens} "
            f"cost=${self.cost:.4f} latency={self.latency_ms}ms"
        )

    def pretty_print(
        self,
        file: Optional[TextIO] = None,
        *,
        truncate: Optional[int] = 800,
    ) -> None:
        """Print a sectioned, human-readable view of this event.

        `truncate=` caps the prompt/response bodies (chars). Pass `None` to
        show full content. Default `sys.stdout`.
        """
        out = file if file is not None else sys.stdout
        lines: List[str] = []

        status = (
            f"ERROR ({self.error_kind.value})" if self.error_kind is not None else "OK"
        )
        lines.append(f"━━━━ Event {self.event_id} [{status}] ━━━━")
        lines.append(f"  timestamp:   {self.timestamp.isoformat()}")
        lines.append(f"  model:       {self.model} ({self.provider})")
        if self.correlation_id:
            lines.append(f"  correlation: {self.correlation_id}")
        if self.parent_request_id:
            lines.append(f"  parent:      {self.parent_request_id}")
        if self.metadata.get("environment"):
            lines.append(f"  environment: {self.metadata['environment']}")

        lines.append("")
        lines.append(
            f"  tokens:    in={self.input_tokens}  out={self.output_tokens}  "
            f"total={self.total_tokens}"
        )
        lines.append(f"  cost:      ${self.cost:.6f}")
        lines.append(f"  latency:   {self.latency_ms}ms")
        if self.time_to_first_token_ms is not None:
            lines.append(f"  ttft:      {self.time_to_first_token_ms}ms")

        if self.prompt is not None:
            lines.append("")
            lines.append("  ── input ──")
            lines.append(_truncate(self.prompt, truncate))

        if self.response is not None:
            lines.append("")
            lines.append("  ── output ──")
            lines.append(_truncate(self.response, truncate))

        if self.tool_calls:
            lines.append("")
            lines.append(f"  ── tool_calls ({len(self.tool_calls)}) ──")
            for tc in self.tool_calls:
                lines.append(f"    {json.dumps(tc, default=str)}")

        if self.error_kind is not None:
            lines.append("")
            lines.append("  ── error ──")
            lines.append(f"  kind:    {self.error_kind.value}")
            lines.append(f"  message: {self.error_message or '<no message>'}")

        out.write("\n".join(lines) + "\n")


def _truncate(text: str, limit: Optional[int]) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"
