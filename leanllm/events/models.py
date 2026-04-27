from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

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
    cost: float           # USD
    latency_ms: int

    parameters: Dict[str, Any] = Field(default_factory=dict)
    tools: Optional[List[Dict[str, Any]]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    time_to_first_token_ms: Optional[int] = None
    total_stream_time_ms: Optional[int] = None

    error_kind: Optional[ErrorKind] = None
    error_message: Optional[str] = None

    labels: Dict[str, str] = Field(default_factory=dict)

    prompt: Optional[str] = None    # stored only when capture_content=True
    response: Optional[str] = None  # stored only when capture_content=True

    normalized_input: Optional[NormalizedInput] = None    # populated by module 4
    normalized_output: Optional[NormalizedOutput] = None  # populated by module 4

    metadata: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }
