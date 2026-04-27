from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .events.models import LLMEvent


class ToolCallRecord(BaseModel):
    """Typed representation of one tool call (vs. the raw dicts on `LLMEvent.tool_calls`)."""

    tool_call_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: Optional[int] = None
    result: Optional[str] = None


class ExecutionNode(BaseModel):
    """One event in an execution tree, with aggregated subtree metrics."""

    event_id: str
    parent_request_id: Optional[str] = None
    correlation_id: Optional[str] = None

    model: str
    provider: str

    cost: float = 0.0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    children: List["ExecutionNode"] = Field(default_factory=list)

    subtree_cost: float = 0.0
    subtree_latency_ms: int = 0
    subtree_tokens: int = 0


class ExecutionGraph(BaseModel):
    """A set of related execution trees — typically one per correlation_id."""

    correlation_id: Optional[str] = None
    roots: List[ExecutionNode] = Field(default_factory=list)

    def flatten(self) -> List[ExecutionNode]:
        """All nodes in DFS pre-order, roots first."""
        out: List[ExecutionNode] = []
        stack: List[ExecutionNode] = list(reversed(self.roots))
        while stack:
            node = stack.pop()
            out.append(node)
            for child in reversed(node.children):
                stack.append(child)
        return out

    def to_ordered_steps(self) -> List[ExecutionNode]:
        """Alias for `flatten()` — reads better when you want execution order."""
        return self.flatten()

    def total_cost(self) -> float:
        return sum(root.subtree_cost for root in self.roots)

    def total_latency_ms(self) -> int:
        return sum(root.subtree_latency_ms for root in self.roots)

    def total_tokens(self) -> int:
        return sum(root.subtree_tokens for root in self.roots)


def parse_tool_calls(*, raw: Optional[List[Dict[str, Any]]]) -> List[ToolCallRecord]:
    """Convert `LLMEvent.tool_calls` raw dicts into typed records.

    Accepts the LiteLLM/OpenAI shape (`{id, type, function: {name, arguments}}`)
    and the flat shape (`{name, arguments}`). Arguments may be a dict or a JSON
    string — both are handled. Empty / `None` input returns `[]`.
    """
    if not raw:
        return []
    records: List[ToolCallRecord] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        tool_call_id = tc.get("id") or tc.get("tool_call_id")
        function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = function.get("name") or tc.get("name") or tc.get("tool_name") or "unknown"
        args_raw = function.get("arguments") if function else tc.get("arguments")
        arguments: Dict[str, Any] = {}
        if isinstance(args_raw, dict):
            arguments = args_raw
        elif isinstance(args_raw, str):
            try:
                parsed = json.loads(args_raw)
                if isinstance(parsed, dict):
                    arguments = parsed
            except json.JSONDecodeError:
                pass
        records.append(
            ToolCallRecord(
                tool_call_id=tool_call_id,
                tool_name=name,
                arguments=arguments,
                execution_time_ms=tc.get("execution_time_ms"),
                result=tc.get("result"),
            )
        )
    return records


def _node_from_event(*, event: LLMEvent) -> ExecutionNode:
    return ExecutionNode(
        event_id=event.event_id,
        parent_request_id=event.parent_request_id,
        correlation_id=event.correlation_id,
        model=event.model,
        provider=event.provider,
        cost=event.cost,
        latency_ms=event.latency_ms,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        total_tokens=event.total_tokens,
        tool_calls=parse_tool_calls(raw=event.tool_calls),
    )


def _compute_subtree_metrics(*, node: ExecutionNode) -> None:
    child_cost = 0.0
    child_latency = 0
    child_tokens = 0
    for child in node.children:
        _compute_subtree_metrics(node=child)
        child_cost += child.subtree_cost
        child_latency += child.subtree_latency_ms
        child_tokens += child.subtree_tokens
    node.subtree_cost = node.cost + child_cost
    node.subtree_latency_ms = node.latency_ms + child_latency
    node.subtree_tokens = node.total_tokens + child_tokens


def build_execution_graphs(*, events: List[LLMEvent]) -> List[ExecutionGraph]:
    """Group events by `correlation_id` and build one `ExecutionGraph` per group.

    - Within each group, nodes are linked by `parent_request_id`.
    - Events whose `parent_request_id` is missing, `None`, or points outside the
      group become roots.
    - Events without `correlation_id` share a single "None" group.
    - Returned graphs are sorted by correlation_id (None last).
    """
    if not events:
        return []

    groups: Dict[Optional[str], List[LLMEvent]] = {}
    for ev in events:
        groups.setdefault(ev.correlation_id, []).append(ev)

    graphs: List[ExecutionGraph] = []
    for correlation_id, group_events in groups.items():
        nodes: Dict[str, ExecutionNode] = {
            ev.event_id: _node_from_event(event=ev) for ev in group_events
        }
        roots: List[ExecutionNode] = []
        for ev in group_events:
            node = nodes[ev.event_id]
            parent_id = ev.parent_request_id
            parent_node = nodes.get(parent_id) if parent_id is not None else None
            if parent_node is None:
                roots.append(node)
            else:
                parent_node.children.append(node)

        for root in roots:
            _compute_subtree_metrics(node=root)

        graphs.append(ExecutionGraph(correlation_id=correlation_id, roots=roots))

    graphs.sort(key=lambda g: (g.correlation_id is None, g.correlation_id or ""))
    return graphs


class Chain:
    """Helper: advance `parent_request_id` across a sequence of LLM calls.

    Typical usage:
        chain = Chain()
        client = LeanLLM(api_key="x", post_call_hook=chain)
        r1 = client.chat(model=..., messages=..., **chain.kwargs())
        r2 = client.chat(model=..., messages=..., **chain.kwargs())
        # r2's event.parent_request_id == r1's event.event_id

    Pass `Chain` directly as `post_call_hook` — it implements `__call__(event)`
    and advances automatically. Or call `chain.record(event=...)` manually.
    """

    def __init__(self, *, correlation_id: Optional[str] = None) -> None:
        self._correlation_id = correlation_id or str(uuid.uuid4())
        self._last_request_id: Optional[str] = None

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    @property
    def last_request_id(self) -> Optional[str]:
        return self._last_request_id

    def record(self, *, event: LLMEvent) -> None:
        if event.correlation_id is None or event.correlation_id == self._correlation_id:
            self._last_request_id = event.event_id

    def kwargs(self) -> Dict[str, Any]:
        return {
            "correlation_id": self._correlation_id,
            "parent_request_id": self._last_request_id,
        }

    def reset(self) -> None:
        self._last_request_id = None

    def __call__(self, event: LLMEvent) -> None:
        self.record(event=event)


ExecutionNode.model_rebuild()
