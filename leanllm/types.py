from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, str]]
    labels: dict[str, str] = Field(default_factory=dict)
    extra_kwargs: dict[str, Any] = Field(default_factory=dict)


class UsageEvent(BaseModel):
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float
    labels: dict[str, str] = Field(default_factory=dict)
