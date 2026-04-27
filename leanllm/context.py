from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from pydantic import BaseModel, Field


_current_context: contextvars.ContextVar[Optional["LeanLLMContext"]] = contextvars.ContextVar(
    "leanllm_context", default=None,
)


class LeanLLMContext(BaseModel):
    """
    Execution context that propagates across nested LLM calls.

    Propagation uses `contextvars.ContextVar`, which gives us thread-isolated
    storage in sync code and automatic inheritance across asyncio tasks spawned
    from the same parent.
    """

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    feature: Optional[str] = None
    environment: Optional[str] = None
    custom_tags: Dict[str, str] = Field(default_factory=dict)

    correlation_id: Optional[str] = None
    parent_request_id: Optional[str] = None

    def merged_labels(self, *, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Flatten identity + custom_tags + per-call extras into a label dict."""
        merged: Dict[str, str] = {}
        if self.user_id is not None:
            merged["user_id"] = self.user_id
        if self.session_id is not None:
            merged["session_id"] = self.session_id
        if self.feature is not None:
            merged["feature"] = self.feature
        if self.environment is not None:
            merged["environment"] = self.environment
        merged.update(self.custom_tags)
        if extra:
            merged.update(extra)
        return merged

    def merge(self, *, other: "LeanLLMContext") -> "LeanLLMContext":
        """Return a new context: `other`'s non-None fields win; custom_tags unioned."""
        return LeanLLMContext(
            user_id=other.user_id if other.user_id is not None else self.user_id,
            session_id=other.session_id if other.session_id is not None else self.session_id,
            feature=other.feature if other.feature is not None else self.feature,
            environment=other.environment if other.environment is not None else self.environment,
            custom_tags={**self.custom_tags, **other.custom_tags},
            correlation_id=(
                other.correlation_id if other.correlation_id is not None else self.correlation_id
            ),
            parent_request_id=(
                other.parent_request_id
                if other.parent_request_id is not None
                else self.parent_request_id
            ),
        )


def set_global_context(*, context: LeanLLMContext) -> None:
    """Set the process-wide default context. Thread-isolated by ContextVar."""
    _current_context.set(context)


def get_current_context() -> Optional[LeanLLMContext]:
    """Return the context bound to the current task/thread, or None."""
    return _current_context.get()


def clear_current_context() -> None:
    _current_context.set(None)


@contextmanager
def use_context(*, context: LeanLLMContext) -> Iterator[LeanLLMContext]:
    """Scoped context override. Nested calls within the with-block inherit the merge."""
    base = _current_context.get()
    effective = base.merge(other=context) if base is not None else context
    token = _current_context.set(effective)
    try:
        yield effective
    finally:
        _current_context.reset(token)


@contextmanager
def trace(*, correlation_id: Optional[str] = None) -> Iterator[LeanLLMContext]:
    """Start a correlation trace. All LLM calls inside share the correlation_id."""
    base = _current_context.get() or LeanLLMContext()
    resolved_id = correlation_id or base.correlation_id or str(uuid.uuid4())
    scoped = base.model_copy(update={"correlation_id": resolved_id})
    token = _current_context.set(scoped)
    try:
        yield scoped
    finally:
        _current_context.reset(token)
