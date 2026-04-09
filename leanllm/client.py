from __future__ import annotations

import time
from typing import Any

import litellm

from .proxy import chat_completion
from .tracker import track_event
from .types import ChatRequest, UsageEvent


class LeanLLM:
    """Lightweight LLM client with built-in usage tracking."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        labels: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> litellm.ModelResponse:
        """Send a chat completion request and track usage."""
        req = ChatRequest(
            model=model,
            messages=messages,
            labels=labels or {},
            extra_kwargs=kwargs,
        )

        start = time.perf_counter()
        response = chat_completion(
            model=req.model,
            messages=req.messages,
            api_key=self.api_key,
            **req.extra_kwargs,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        usage = getattr(response, "usage", None)
        event = UsageEvent(
            model=req.model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            latency_ms=round(latency_ms, 2),
            labels=req.labels,
        )
        track_event(event)

        return response

    def completion(
        self,
        model: str,
        prompt: str,
        labels: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> litellm.ModelResponse:
        """Convenience wrapper: single prompt → chat completion."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(model=model, messages=messages, labels=labels, **kwargs)
