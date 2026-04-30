"""LeanLLM — lightweight LLM wrapper with async event pipeline and usage tracking."""

from typing import Any, Optional

from .client import LeanLLM
from .config import LeanLLMConfig
from .context import LeanLLMContext, set_global_context, trace, use_context
from .events.models import LLMEvent
from .lineage import (
    Chain,
    ExecutionGraph,
    ExecutionNode,
    ToolCallRecord,
    build_execution_graphs,
    parse_tool_calls,
)
from .replay import ReplayEngine, ReplayOverrides, ReplayResult


# Module 16 — process-wide singleton convenience for scripts / notebooks.
# Libraries should construct LeanLLM(...) explicitly and not rely on this.
_default_client: Optional[LeanLLM] = None
_INIT_REQUIRED_MSG = (
    "leanllm.init(api_key=...) must be called before leanllm.chat / leanllm.completion."
)


def init(
    api_key: str = "",
    config: Optional[LeanLLMConfig] = None,
    **kwargs: Any,
) -> LeanLLM:
    """Initialize a process-wide default LeanLLM client.

    Subsequent calls return the same instance (idempotent). To rebuild,
    call `leanllm.shutdown()` first.

    Convenience for scripts and notebooks. Libraries should keep using
    `LeanLLM(api_key=...)` explicitly.
    """
    global _default_client
    if _default_client is None:
        _default_client = LeanLLM(api_key=api_key, config=config, **kwargs)
    return _default_client


def get_default_client() -> Optional[LeanLLM]:
    """Return the process-wide singleton, or None if `init()` wasn't called."""
    return _default_client


def shutdown() -> None:
    """Stop the singleton's worker (if any) and drop the reference."""
    global _default_client
    if _default_client is not None and _default_client._worker is not None:
        _default_client._worker.stop(timeout=2.0)
    _default_client = None


def chat(*args: Any, **kwargs: Any) -> Any:
    """Top-level shortcut: routes to the singleton's `chat()`."""
    if _default_client is None:
        raise RuntimeError(_INIT_REQUIRED_MSG)
    return _default_client.chat(*args, **kwargs)


def completion(*args: Any, **kwargs: Any) -> Any:
    """Top-level shortcut: routes to the singleton's `completion()`."""
    if _default_client is None:
        raise RuntimeError(_INIT_REQUIRED_MSG)
    return _default_client.completion(*args, **kwargs)


__all__ = [
    "Chain",
    "ExecutionGraph",
    "ExecutionNode",
    "LeanLLM",
    "LeanLLMConfig",
    "LeanLLMContext",
    "LLMEvent",
    "ReplayEngine",
    "ReplayOverrides",
    "ReplayResult",
    "ToolCallRecord",
    "build_execution_graphs",
    "chat",
    "completion",
    "get_default_client",
    "init",
    "parse_tool_calls",
    "set_global_context",
    "shutdown",
    "trace",
    "use_context",
]
__version__ = "1.0.0"
