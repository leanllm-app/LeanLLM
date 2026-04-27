from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .config import LeanLLMConfig
from .context import LeanLLMContext, get_current_context
from .events.cost import CostCalculator, estimate_tokens, extract_provider
from .events.models import ErrorKind, LLMEvent
from .events.queue import EventQueue
from .events.worker import EventWorker
from .normalizer import (
    LengthBucket,
    NormalizedInput,
    NormalizedOutput,
    OutputType,
    normalize_input,
    normalize_output,
)
from .proxy import chat_completion
from .redaction import RedactionMode, RedactionPolicy, apply as apply_redaction
from .storage import create_store

logger = logging.getLogger(__name__)


_CAPTURED_PARAMETERS = frozenset({
    "temperature", "max_tokens", "top_p", "frequency_penalty",
    "presence_penalty", "stop", "n", "seed", "response_format",
    "user", "logprobs", "top_logprobs", "stream",
})


PreCallHook = Callable[[Dict[str, Any]], None]
PostCallHook = Callable[[LLMEvent], None]
ErrorHook = Callable[[Exception, Dict[str, Any]], None]


def _classify_error(exc: Exception) -> ErrorKind:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return ErrorKind.TIMEOUT
    if "ratelimit" in name or "rate_limit" in name:
        return ErrorKind.RATE_LIMIT
    if "parse" in name or "json" in name or "validation" in name:
        return ErrorKind.PARSING_ERROR
    if "api" in name or "provider" in name or "connection" in name:
        return ErrorKind.PROVIDER_ERROR
    return ErrorKind.UNKNOWN


def _tool_call_to_dict(tc: Any) -> Dict[str, Any]:
    if isinstance(tc, dict):
        return tc
    model_dump = getattr(tc, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return {"raw": repr(tc)}


class LeanLLM:
    """
    Lightweight LLM client with a non-blocking async event pipeline.

    Flow:
        chat() / completion()
            → LiteLLM call (streaming or not)
            → LLMEvent built (including errors)
            → enqueued (non-blocking, thread-safe)
            → background worker batch-inserts into the configured store
    """

    def __init__(
        self,
        api_key: str,
        config: Optional[LeanLLMConfig] = None,
        *,
        pre_call_hook: Optional[PreCallHook] = None,
        post_call_hook: Optional[PostCallHook] = None,
        error_hook: Optional[ErrorHook] = None,
    ) -> None:
        self.api_key = api_key
        self._config = config or LeanLLMConfig.from_env()
        self._cost = CostCalculator()
        self._pre_call_hook = pre_call_hook
        self._post_call_hook = post_call_hook
        self._error_hook = error_hook

        self._queue: Optional[EventQueue] = None
        self._worker: Optional[EventWorker] = None

        has_destination = self._config.database_url or self._config.leanllm_api_key

        if self._config.enable_persistence and has_destination:
            self._queue = EventQueue(max_size=self._config.queue_max_size)
            store = create_store(
                database_url=self._config.database_url,
                api_key=self._config.leanllm_api_key,
                endpoint=self._config.endpoint,
                auto_migrate=self._config.auto_migrate,
            )
            self._worker = EventWorker(
                queue=self._queue,
                store=store,
                batch_size=self._config.batch_size,
                flush_interval_ms=self._config.flush_interval_ms,
            )
            self._worker.start()
        elif self._config.enable_persistence:
            logger.info(
                "[LeanLLM] No LEANLLM_DATABASE_URL or LEANLLM_API_KEY set — "
                "events will not be persisted."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        labels: Optional[Dict[str, str]] = None,
        *,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_request_id: Optional[str] = None,
        context: Optional[LeanLLMContext] = None,
        **kwargs: Any,
    ) -> Any:
        """Send a chat completion request.

        Returns the raw LiteLLM `ModelResponse` for non-streaming calls,
        or an iterator of chunks when `stream=True`.
        """
        ambient = context if context is not None else get_current_context()
        effective_correlation = correlation_id or (
            ambient.correlation_id if ambient is not None else None
        )
        effective_parent = parent_request_id or (
            ambient.parent_request_id if ambient is not None else None
        )
        merged_labels = (
            ambient.merged_labels(extra=labels) if ambient is not None else (labels or {})
        )

        stream = bool(kwargs.get("stream"))
        pre_call = self._pre_call_snapshot(
            model=model,
            messages=messages,
            kwargs=kwargs,
            request_id=request_id,
            correlation_id=effective_correlation,
            parent_request_id=effective_parent,
        )
        self._fire_pre_call(snapshot=pre_call)

        if stream:
            return self._chat_stream(
                pre_call=pre_call,
                messages=messages,
                labels=merged_labels,
                kwargs=kwargs,
            )

        start = time.perf_counter()
        try:
            response = chat_completion(
                model=model, messages=messages, api_key=self.api_key, **kwargs,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._emit_error(
                pre_call=pre_call, labels=merged_labels,
                latency_ms=latency_ms, exc=exc,
            )
            self._fire_error(exc=exc, pre_call=pre_call)
            raise

        latency_ms = int((time.perf_counter() - start) * 1000)
        self._emit(
            pre_call=pre_call, labels=merged_labels,
            response=response, latency_ms=latency_ms,
        )
        return response

    def completion(
        self,
        model: str,
        prompt: str,
        labels: Optional[Dict[str, str]] = None,
        *,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_request_id: Optional[str] = None,
        context: Optional[LeanLLMContext] = None,
        **kwargs: Any,
    ) -> Any:
        """Convenience wrapper: single string prompt → chat completion."""
        return self.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            labels=labels,
            request_id=request_id,
            correlation_id=correlation_id,
            parent_request_id=parent_request_id,
            context=context,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _chat_stream(
        self,
        *,
        pre_call: Dict[str, Any],
        messages: List[Dict[str, str]],
        labels: Dict[str, str],
        kwargs: Dict[str, Any],
    ) -> Iterator[Any]:
        start = time.perf_counter()
        try:
            iterator = chat_completion(
                model=pre_call["model"], messages=messages,
                api_key=self.api_key, **kwargs,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._emit_error(
                pre_call=pre_call, labels=labels,
                latency_ms=latency_ms, exc=exc,
            )
            self._fire_error(exc=exc, pre_call=pre_call)
            raise

        return self._wrap_stream(
            iterator=iterator, pre_call=pre_call, labels=labels, start=start,
        )

    def _wrap_stream(
        self,
        *,
        iterator: Iterator[Any],
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        start: float,
    ) -> Iterator[Any]:
        first_token_at: Optional[float] = None
        chunks: List[Any] = []
        error: Optional[Exception] = None
        try:
            for chunk in iterator:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                chunks.append(chunk)
                yield chunk
        except Exception as exc:
            error = exc
            raise
        finally:
            end = time.perf_counter()
            total_ms = int((end - start) * 1000)
            ttft_ms = (
                int((first_token_at - start) * 1000)
                if first_token_at is not None
                else None
            )
            if error is None:
                self._emit_stream(
                    pre_call=pre_call, labels=labels, chunks=chunks,
                    latency_ms=total_ms, ttft_ms=ttft_ms,
                )
            else:
                self._emit_error(
                    pre_call=pre_call, labels=labels,
                    latency_ms=total_ms, exc=error,
                )
                self._fire_error(exc=error, pre_call=pre_call)

    # ------------------------------------------------------------------
    # Hooks — never raise into the caller
    # ------------------------------------------------------------------

    def _fire_pre_call(self, *, snapshot: Dict[str, Any]) -> None:
        if self._pre_call_hook is None:
            return
        try:
            self._pre_call_hook(snapshot)
        except Exception:
            logger.exception(
                "[LeanLLM] pre_call_hook raised: request_id=%s model=%s",
                snapshot.get("request_id"), snapshot.get("model"),
            )

    def _fire_post_call(self, *, event: LLMEvent) -> None:
        if self._post_call_hook is None:
            return
        try:
            self._post_call_hook(event)
        except Exception:
            logger.exception(
                "[LeanLLM] post_call_hook raised: event_id=%s model=%s",
                event.event_id, event.model,
            )

    def _fire_error(self, *, exc: Exception, pre_call: Dict[str, Any]) -> None:
        if self._error_hook is None:
            return
        try:
            self._error_hook(exc, pre_call)
        except Exception:
            logger.exception(
                "[LeanLLM] error_hook raised: request_id=%s model=%s orig=%s",
                pre_call.get("request_id"), pre_call.get("model"), exc,
            )

    # ------------------------------------------------------------------
    # Emission — builds and enqueues events. Never blocks or raises.
    # ------------------------------------------------------------------

    def _emit(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        response: Any,
        latency_ms: int,
    ) -> None:
        try:
            event = self._build_event_from_response(
                pre_call=pre_call, labels=labels,
                response=response, latency_ms=latency_ms,
            )
        except Exception:
            logger.exception("[LeanLLM] Failed to build event — skipping.")
            return
        self._enqueue(event=event)
        self._fire_post_call(event=event)

    def _emit_stream(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        chunks: List[Any],
        latency_ms: int,
        ttft_ms: Optional[int],
    ) -> None:
        try:
            event = self._build_event_from_stream(
                pre_call=pre_call, labels=labels, chunks=chunks,
                latency_ms=latency_ms, ttft_ms=ttft_ms,
            )
        except Exception:
            logger.exception("[LeanLLM] Failed to build stream event — skipping.")
            return
        self._enqueue(event=event)
        self._fire_post_call(event=event)

    def _emit_error(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        latency_ms: int,
        exc: Exception,
    ) -> None:
        try:
            event = self._build_error_event(
                pre_call=pre_call, labels=labels,
                latency_ms=latency_ms, exc=exc,
            )
        except Exception:
            logger.exception("[LeanLLM] Failed to build error event — skipping.")
            return
        self._enqueue(event=event)

    def _enqueue(self, *, event: LLMEvent) -> None:
        if self._queue is None:
            return
        self._queue.enqueue(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pre_call_snapshot(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        kwargs: Dict[str, Any],
        request_id: Optional[str],
        correlation_id: Optional[str],
        parent_request_id: Optional[str],
    ) -> Dict[str, Any]:
        parameters = {k: v for k, v in kwargs.items() if k in _CAPTURED_PARAMETERS}
        tools = kwargs.get("tools") or kwargs.get("functions")
        return {
            "request_id": request_id or str(uuid.uuid4()),
            "correlation_id": correlation_id,
            "parent_request_id": parent_request_id,
            "model": model,
            "provider": extract_provider(model),
            "messages": messages,
            "parameters": parameters,
            "tools": tools,
        }

    def _capture_content(
        self,
        *,
        messages: List[Dict[str, str]],
        content: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        # Redaction policy controls content capture behavior
        policy = RedactionPolicy(mode=self._config.redaction_mode)

        # Build raw content
        prompt_text = json.dumps(messages) if messages else None
        response_text = content

        # Apply redaction based on policy
        prompt_text = apply_redaction(policy=policy, text=prompt_text)
        response_text = apply_redaction(policy=policy, text=response_text)

        return prompt_text, response_text

    def _normalize(
        self,
        *,
        messages: List[Dict[str, str]],
        content: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]],
    ) -> Tuple[Optional[NormalizedInput], Optional[NormalizedOutput]]:
        if not self._config.auto_normalize:
            return None, None
        n_input = normalize_input(messages=messages, auto_tag=True)
        if content is not None:
            n_output = normalize_output(text=content, auto_tag=True)
        elif tool_calls:
            n_output = NormalizedOutput(
                output_type=OutputType.TOOL_CALL,
                length_bucket=LengthBucket.SHORT,
            )
        else:
            n_output = None
        return n_input, n_output

    def _build_event_from_response(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        response: Any,
        latency_ms: int,
    ) -> LLMEvent:
        usage = getattr(response, "usage", None)
        input_tokens: int = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens: int = getattr(usage, "completion_tokens", 0) or 0

        content: Optional[str] = None
        finish_reason: Optional[str] = None
        tool_calls: Optional[List[Dict[str, Any]]] = None

        choices = getattr(response, "choices", None) or []
        if choices:
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            message = getattr(choice, "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                raw_tool_calls = getattr(message, "tool_calls", None)
                if raw_tool_calls:
                    tool_calls = [_tool_call_to_dict(tc) for tc in raw_tool_calls]

        if input_tokens == 0 and pre_call["messages"]:
            text = " ".join(
                m.get("content", "") for m in pre_call["messages"] if m.get("content")
            )
            input_tokens = estimate_tokens(text, pre_call["model"])

        if output_tokens == 0 and content:
            output_tokens = estimate_tokens(content, pre_call["model"])

        total_tokens = input_tokens + output_tokens
        cost = self._cost.calculate(pre_call["model"], input_tokens, output_tokens)

        prompt_text, response_text = self._capture_content(
            messages=pre_call["messages"], content=content,
        )
        normalized_input, normalized_output = self._normalize(
            messages=pre_call["messages"], content=content, tool_calls=tool_calls,
        )

        return LLMEvent(
            event_id=pre_call["request_id"],
            correlation_id=pre_call["correlation_id"],
            parent_request_id=pre_call["parent_request_id"],
            model=pre_call["model"],
            provider=pre_call["provider"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            latency_ms=latency_ms,
            parameters=pre_call["parameters"],
            tools=pre_call["tools"],
            tool_calls=tool_calls,
            labels=labels,
            prompt=prompt_text,
            response=response_text,
            normalized_input=normalized_input,
            normalized_output=normalized_output,
            metadata={"finish_reason": finish_reason},
        )

    def _build_event_from_stream(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        chunks: List[Any],
        latency_ms: int,
        ttft_ms: Optional[int],
    ) -> LLMEvent:
        text_parts: List[str] = []
        tool_calls_raw: List[Dict[str, Any]] = []
        finish_reason: Optional[str] = None
        input_tokens = 0
        output_tokens = 0

        for chunk in chunks:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_count = getattr(usage, "prompt_tokens", 0) or 0
                completion_count = getattr(usage, "completion_tokens", 0) or 0
                if prompt_count:
                    input_tokens = prompt_count
                if completion_count:
                    output_tokens = completion_count
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None) or getattr(choice, "message", None)
            if delta is not None:
                piece = getattr(delta, "content", None)
                if piece:
                    text_parts.append(piece)
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for tc in delta_tool_calls:
                        tool_calls_raw.append(_tool_call_to_dict(tc))
            reason = getattr(choice, "finish_reason", None)
            if reason:
                finish_reason = reason

        full_text = "".join(text_parts) if text_parts else None

        if input_tokens == 0 and pre_call["messages"]:
            text = " ".join(
                m.get("content", "") for m in pre_call["messages"] if m.get("content")
            )
            input_tokens = estimate_tokens(text, pre_call["model"])

        if output_tokens == 0 and full_text:
            output_tokens = estimate_tokens(full_text, pre_call["model"])

        total_tokens = input_tokens + output_tokens
        cost = self._cost.calculate(pre_call["model"], input_tokens, output_tokens)

        prompt_text, response_text = self._capture_content(
            messages=pre_call["messages"], content=full_text,
        )
        normalized_input, normalized_output = self._normalize(
            messages=pre_call["messages"],
            content=full_text,
            tool_calls=tool_calls_raw or None,
        )

        return LLMEvent(
            event_id=pre_call["request_id"],
            correlation_id=pre_call["correlation_id"],
            parent_request_id=pre_call["parent_request_id"],
            model=pre_call["model"],
            provider=pre_call["provider"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            latency_ms=latency_ms,
            time_to_first_token_ms=ttft_ms,
            total_stream_time_ms=latency_ms,
            parameters=pre_call["parameters"],
            tools=pre_call["tools"],
            tool_calls=tool_calls_raw or None,
            labels=labels,
            prompt=prompt_text,
            response=response_text,
            normalized_input=normalized_input,
            normalized_output=normalized_output,
            metadata={"finish_reason": finish_reason, "stream": True},
        )

    def _build_error_event(
        self,
        *,
        pre_call: Dict[str, Any],
        labels: Dict[str, str],
        latency_ms: int,
        exc: Exception,
    ) -> LLMEvent:
        prompt_text, _ = self._capture_content(
            messages=pre_call["messages"], content=None,
        )
        return LLMEvent(
            event_id=pre_call["request_id"],
            correlation_id=pre_call["correlation_id"],
            parent_request_id=pre_call["parent_request_id"],
            model=pre_call["model"],
            provider=pre_call["provider"],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cost=0.0,
            latency_ms=latency_ms,
            parameters=pre_call["parameters"],
            tools=pre_call["tools"],
            labels=labels,
            prompt=prompt_text,
            error_kind=_classify_error(exc),
            error_message=str(exc),
            metadata={"error_class": exc.__class__.__name__},
        )
