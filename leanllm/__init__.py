"""LeanLLM — lightweight LLM wrapper with async event pipeline and usage tracking."""

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
    "parse_tool_calls",
    "set_global_context",
    "trace",
    "use_context",
]
__version__ = "0.3.0"
