from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from pydantic import BaseModel, Field


_current_context: contextvars.ContextVar[Optional["LeanLLMContext"]] = (
    contextvars.ContextVar(
        "leanllm_context",
        default=None,
    )
)

# Module 16 — auto-chain bookkeeping. Holds the last emitted event_id of the
# current async task / sync stack so the next chat() can auto-fill
# parent_request_id when `config.auto_chain=True`. Internal: never read or
# written by user code; not part of LeanLLMContext on purpose to avoid polluting
# the user-facing model with worker bookkeeping.
_auto_chain_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "leanllm_auto_chain",
    default=None,
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

    def merged_labels(
        self, *, extra: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
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
            session_id=other.session_id
            if other.session_id is not None
            else self.session_id,
            feature=other.feature if other.feature is not None else self.feature,
            environment=other.environment
            if other.environment is not None
            else self.environment,
            custom_tags={**self.custom_tags, **other.custom_tags},
            correlation_id=(
                other.correlation_id
                if other.correlation_id is not None
                else self.correlation_id
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


def get_auto_chain_parent() -> Optional[str]:
    """Return the last emitted event_id in the current task, or None."""
    return _auto_chain_var.get()


def set_auto_chain_parent(*, event_id: Optional[str]) -> None:
    """Internal: advance the auto-chain pointer after emitting an event."""
    _auto_chain_var.set(event_id)


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
    """Start a correlation trace. All LLM calls inside share the correlation_id.

    Also resets the auto-chain pointer for this scope — entering `trace()`
    means "new chain starts here", so the first call inside has no parent.
    """
    base = _current_context.get() or LeanLLMContext()
    resolved_id = correlation_id or base.correlation_id or str(uuid.uuid4())
    scoped = base.model_copy(update={"correlation_id": resolved_id})
    token = _current_context.set(scoped)
    chain_token = _auto_chain_var.set(None)
    try:
        yield scoped
    finally:
        _current_context.reset(token)
        _auto_chain_var.reset(chain_token)
