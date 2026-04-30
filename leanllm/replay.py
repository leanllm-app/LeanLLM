from __future__ import annotations

import difflib
import json
import logging
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TextIO

from pydantic import BaseModel

from .events.models import LLMEvent

if TYPE_CHECKING:
    from .client import LeanLLM

logger = logging.getLogger(__name__)


class ReplayOverrides(BaseModel):
    """Optional overrides applied on top of the original event when replaying."""

    model: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    tools: Optional[List[Dict[str, Any]]] = None


class ReplayResult(BaseModel):
    """Outcome of a replay — original side by side with the new call."""

    original_request_id: str
    new_request_id: Optional[str] = None
    error_message: Optional[str] = None

    text_before: Optional[str] = None
    text_after: Optional[str] = None
    text_diff: Optional[str] = None
    text_identical: bool = False

    tokens_before: int = 0
    tokens_after: int = 0
    tokens_delta: int = 0

    latency_ms_before: int = 0
    latency_ms_after: int = 0
    latency_ms_delta: int = 0

    def summary(self) -> str:
        """One-line, human-readable description of this replay outcome."""
        if self.error_message:
            return f"replay {self.original_request_id} → ERROR: {self.error_message}"
        marker = "≡ identical" if self.text_identical else "Δ different"
        return (
            f"replay {self.original_request_id} → {self.new_request_id}: "
            f"text {marker}, tokens {self.tokens_delta:+d}, "
            f"latency {self.latency_ms_delta:+d}ms"
        )

    def pretty_print(self, file: Optional[TextIO] = None) -> None:
        """Print a sectioned view of this replay result, including any diff."""
        out = file if file is not None else sys.stdout
        lines: List[str] = []
        lines.append(f"━━━━ Replay {self.original_request_id} ━━━━")
        if self.error_message:
            lines.append("  status:  ERROR")
            lines.append(f"  message: {self.error_message}")
            out.write("\n".join(lines) + "\n")
            return

        lines.append(f"  new_request_id: {self.new_request_id}")
        lines.append(
            f"  text:     {'identical' if self.text_identical else 'different'}"
        )
        lines.append(
            f"  tokens:   before={self.tokens_before}  after={self.tokens_after}  "
            f"delta={self.tokens_delta:+d}"
        )
        lines.append(
            f"  latency:  before={self.latency_ms_before}ms  "
            f"after={self.latency_ms_after}ms  delta={self.latency_ms_delta:+d}ms"
        )
        if self.text_diff:
            lines.append("")
            lines.append("  ── diff ──")
            lines.append(self.text_diff.rstrip("\n"))
        out.write("\n".join(lines) + "\n")


class ReplayEngine:
    """Deterministic replay of past LLM events.

    Runs a replayed call through an existing sync `LeanLLM` client, so the
    replayed event flows through the normal pipeline (post_call_hook,
    persistence, etc.). The engine itself is stateless.
    """

    def __init__(self, *, client: "LeanLLM") -> None:
        self._client = client

    def replay(
        self,
        *,
        event: LLMEvent,
        overrides: Optional[ReplayOverrides] = None,
    ) -> ReplayResult:
        messages = self._resolve_messages(event=event, overrides=overrides)
        model = (
            overrides.model
            if overrides is not None and overrides.model is not None
            else event.model
        )
        parameters = (
            overrides.parameters
            if overrides is not None and overrides.parameters is not None
            else (event.parameters or {})
        )
        tools = (
            overrides.tools
            if overrides is not None and overrides.tools is not None
            else event.tools
        )

        new_request_id = str(uuid.uuid4())
        call_kwargs: Dict[str, Any] = {
            k: v for k, v in parameters.items() if k != "stream"
        }
        if tools:
            call_kwargs["tools"] = tools

        start = time.perf_counter()
        response = self._client.chat(
            model=model,
            messages=messages,
            request_id=new_request_id,
            **call_kwargs,
        )
        new_latency_ms = int((time.perf_counter() - start) * 1000)

        return self._compare(
            original=event,
            new_text=self._extract_text(response=response),
            new_tokens=self._extract_total_tokens(response=response),
            new_latency_ms=new_latency_ms,
            new_request_id=new_request_id,
        )

    async def replay_by_id(
        self,
        *,
        event_id: str,
        overrides: Optional[ReplayOverrides] = None,
    ) -> ReplayResult:
        """Fetch an event from the configured store, then replay it.

        Convenience wrapper for the common "I have a request_id, run it again"
        flow. Async because storage reads are async (Module 12); the actual
        replay still goes through the sync `chat()` path.
        """
        event = await self._client.get_event(event_id=event_id)
        if event is None:
            raise ValueError(f"Event {event_id} not found in the configured store.")
        return self.replay(event=event, overrides=overrides)

    def replay_batch(
        self,
        *,
        events: List[LLMEvent],
        overrides: Optional[ReplayOverrides] = None,
        max_workers: int = 4,
    ) -> List[ReplayResult]:
        """Replay many events concurrently; per-item failures become `error_message` results."""
        if not events:
            return []

        results: List[ReplayResult] = [None] * len(events)  # type: ignore[list-item]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_index = {
                pool.submit(self.replay, event=event, overrides=overrides): idx
                for idx, event in enumerate(events)
            }
            for future in future_to_index:
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    logger.warning(
                        "[LeanLLM] Replay failed: original_id=%s err=%s",
                        events[idx].event_id,
                        exc,
                    )
                    results[idx] = ReplayResult(
                        original_request_id=events[idx].event_id,
                        error_message=str(exc),
                        text_before=events[idx].response,
                        tokens_before=events[idx].total_tokens,
                        latency_ms_before=events[idx].latency_ms,
                    )

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_messages(
        self,
        *,
        event: LLMEvent,
        overrides: Optional[ReplayOverrides],
    ) -> List[Dict[str, Any]]:
        if overrides is not None and overrides.messages is not None:
            return overrides.messages
        if event.prompt is None:
            raise ValueError(
                f"Cannot replay event {event.event_id}: messages were not captured "
                "(capture_content=False). Pass ReplayOverrides(messages=[...])."
            )
        try:
            parsed = json.loads(event.prompt)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Cannot replay event {event.event_id}: prompt is not valid JSON."
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(
                f"Cannot replay event {event.event_id}: prompt is not a messages list."
            )
        return parsed

    def _extract_text(self, *, response: Any) -> Optional[str]:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        if message is None:
            return None
        return getattr(message, "content", None)

    def _extract_total_tokens(self, *, response: Any) -> int:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0
        return getattr(usage, "total_tokens", 0) or 0

    def _compare(
        self,
        *,
        original: LLMEvent,
        new_text: Optional[str],
        new_tokens: int,
        new_latency_ms: int,
        new_request_id: str,
    ) -> ReplayResult:
        text_before = original.response
        text_after = new_text
        text_identical = text_before == text_after

        text_diff: Optional[str] = None
        if not text_identical and text_before is not None and text_after is not None:
            text_diff = "".join(
                difflib.unified_diff(
                    text_before.splitlines(keepends=True),
                    text_after.splitlines(keepends=True),
                    fromfile=f"original:{original.event_id}",
                    tofile=f"replay:{new_request_id}",
                    lineterm="",
                )
            )

        return ReplayResult(
            original_request_id=original.event_id,
            new_request_id=new_request_id,
            text_before=text_before,
            text_after=text_after,
            text_diff=text_diff,
            text_identical=text_identical,
            tokens_before=original.total_tokens,
            tokens_after=new_tokens,
            tokens_delta=new_tokens - original.total_tokens,
            latency_ms_before=original.latency_ms,
            latency_ms_after=new_latency_ms,
            latency_ms_delta=new_latency_ms - original.latency_ms,
        )
