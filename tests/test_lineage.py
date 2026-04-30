from __future__ import annotations

from typing import List

from leanllm import (
    Chain,
    ExecutionGraph,
    LeanLLM,
    LeanLLMConfig,
    LLMEvent,
    build_execution_graphs,
    parse_tool_calls,
)
from tests.conftest import make_response


def _ev(
    *,
    event_id: str,
    parent_request_id: str | None = None,
    correlation_id: str | None = None,
    cost: float = 1.0,
    latency_ms: int = 100,
    total_tokens: int = 50,
    tool_calls=None,
) -> LLMEvent:
    return LLMEvent(
        event_id=event_id,
        parent_request_id=parent_request_id,
        correlation_id=correlation_id,
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=total_tokens // 2,
        output_tokens=total_tokens - total_tokens // 2,
        total_tokens=total_tokens,
        cost=cost,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )


# ----------------------------------------------------------------------
# parse_tool_calls
# ----------------------------------------------------------------------


def test_parse_tool_calls_openai_shape_with_string_args():
    raw = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q": "hi"}'},
        }
    ]
    [rec] = parse_tool_calls(raw=raw)
    assert rec.tool_call_id == "call_1"
    assert rec.tool_name == "search"
    assert rec.arguments == {"q": "hi"}


def test_parse_tool_calls_flat_shape_with_dict_args():
    raw = [{"name": "lookup", "arguments": {"id": 7}}]
    [rec] = parse_tool_calls(raw=raw)
    assert rec.tool_name == "lookup"
    assert rec.arguments == {"id": 7}


def test_parse_tool_calls_empty_input_returns_empty():
    assert parse_tool_calls(raw=None) == []
    assert parse_tool_calls(raw=[]) == []


def test_parse_tool_calls_skips_non_dict_entries():
    raw = [{"name": "a"}, "not-a-dict", 42, None]
    records = parse_tool_calls(raw=raw)
    assert len(records) == 1
    assert records[0].tool_name == "a"


def test_parse_tool_calls_malformed_json_args_yield_empty_dict():
    raw = [{"name": "x", "arguments": "{not-json"}]
    [rec] = parse_tool_calls(raw=raw)
    assert rec.arguments == {}


def test_parse_tool_calls_unknown_when_no_name():
    raw = [{"arguments": {}}]
    [rec] = parse_tool_calls(raw=raw)
    assert rec.tool_name == "unknown"


# ----------------------------------------------------------------------
# build_execution_graphs
# ----------------------------------------------------------------------


def test_build_graphs_empty_returns_empty():
    assert build_execution_graphs(events=[]) == []


def test_build_graphs_buckets_by_correlation_id():
    events = [
        _ev(event_id="a1", correlation_id="C1"),
        _ev(event_id="a2", correlation_id="C2"),
        _ev(event_id="a3", correlation_id="C1"),
    ]
    graphs = build_execution_graphs(events=events)
    by_corr = {g.correlation_id: g for g in graphs}
    assert set(by_corr) == {"C1", "C2"}
    assert len(by_corr["C1"].roots) == 2  # both are roots (no parent_request_id)


def test_build_graphs_attaches_children_via_parent_request_id():
    parent = _ev(
        event_id="p", correlation_id="C1", cost=1.0, total_tokens=10, latency_ms=100
    )
    child = _ev(
        event_id="c",
        parent_request_id="p",
        correlation_id="C1",
        cost=2.0,
        total_tokens=20,
        latency_ms=200,
    )
    [graph] = build_execution_graphs(events=[parent, child])
    assert len(graph.roots) == 1
    root = graph.roots[0]
    assert root.event_id == "p"
    assert len(root.children) == 1
    assert root.children[0].event_id == "c"


def test_subtree_metrics_sum_root_plus_children():
    parent = _ev(
        event_id="p", correlation_id="C", cost=1.0, total_tokens=10, latency_ms=100
    )
    child = _ev(
        event_id="c",
        parent_request_id="p",
        correlation_id="C",
        cost=2.0,
        total_tokens=20,
        latency_ms=200,
    )
    [graph] = build_execution_graphs(events=[parent, child])
    root = graph.roots[0]
    assert root.subtree_cost == 3.0
    assert root.subtree_latency_ms == 300
    assert root.subtree_tokens == 30


def test_orphan_event_with_external_parent_becomes_root():
    a = _ev(event_id="a", correlation_id="C1")
    orphan = _ev(event_id="b", parent_request_id="lives-elsewhere", correlation_id="C1")
    [graph] = build_execution_graphs(events=[a, orphan])
    assert {n.event_id for n in graph.roots} == {"a", "b"}


def test_none_correlation_group_sorts_last():
    events = [
        _ev(event_id="x", correlation_id=None),
        _ev(event_id="y", correlation_id="C1"),
        _ev(event_id="z", correlation_id="C2"),
    ]
    graphs = build_execution_graphs(events=events)
    assert graphs[-1].correlation_id is None


def test_flatten_returns_dfs_pre_order():
    parent = _ev(event_id="p", correlation_id="C")
    child1 = _ev(event_id="c1", parent_request_id="p", correlation_id="C")
    grand = _ev(event_id="g1", parent_request_id="c1", correlation_id="C")
    child2 = _ev(event_id="c2", parent_request_id="p", correlation_id="C")
    [graph] = build_execution_graphs(events=[parent, child1, grand, child2])
    ordered = [n.event_id for n in graph.flatten()]
    assert ordered == ["p", "c1", "g1", "c2"]


def test_to_ordered_steps_alias_of_flatten():
    parent = _ev(event_id="p", correlation_id="C")
    child = _ev(event_id="c", parent_request_id="p", correlation_id="C")
    [graph] = build_execution_graphs(events=[parent, child])
    assert graph.flatten() == graph.to_ordered_steps()


def test_total_aggregations_sum_root_subtree_metrics():
    a = _ev(event_id="a", correlation_id="C", cost=1.0, total_tokens=5, latency_ms=10)
    b = _ev(event_id="b", correlation_id="C", cost=2.0, total_tokens=6, latency_ms=20)
    [graph] = build_execution_graphs(events=[a, b])
    assert graph.total_cost() == 3.0
    assert graph.total_tokens() == 11
    assert graph.total_latency_ms() == 30


def test_graph_serialization_roundtrip():
    parent = _ev(event_id="p", correlation_id="C")
    child = _ev(event_id="c", parent_request_id="p", correlation_id="C")
    [graph] = build_execution_graphs(events=[parent, child])
    raw = graph.model_dump(mode="json")
    rebuilt = ExecutionGraph.model_validate(raw)
    assert rebuilt.roots[0].children[0].event_id == "c"


# ----------------------------------------------------------------------
# Chain
# ----------------------------------------------------------------------


def test_chain_first_call_has_no_parent():
    chain = Chain()
    kwargs = chain.kwargs()
    assert kwargs["correlation_id"] == chain.correlation_id
    assert kwargs["parent_request_id"] is None


def test_chain_record_advances_last_request_id():
    chain = Chain(correlation_id="C")
    ev = _ev(event_id="evt-1", correlation_id="C")
    chain.record(event=ev)
    assert chain.last_request_id == "evt-1"
    assert chain.kwargs()["parent_request_id"] == "evt-1"


def test_chain_call_alias_of_record():
    chain = Chain(correlation_id="C")
    ev = _ev(event_id="evt-2", correlation_id="C")
    chain(ev)
    assert chain.last_request_id == "evt-2"


def test_chain_ignores_event_from_other_correlation():
    chain = Chain(correlation_id="OWN")
    ev = _ev(event_id="evt-foreign", correlation_id="OTHER")
    chain.record(event=ev)
    assert chain.last_request_id is None


def test_chain_accepts_event_with_none_correlation():
    chain = Chain(correlation_id="OWN")
    ev = _ev(event_id="evt", correlation_id=None)
    chain.record(event=ev)
    assert chain.last_request_id == "evt"


def test_chain_reset_clears_last_request_id():
    chain = Chain(correlation_id="C")
    chain.record(event=_ev(event_id="evt", correlation_id="C"))
    chain.reset()
    assert chain.last_request_id is None
    assert chain.kwargs()["parent_request_id"] is None


def test_chain_as_post_call_hook_advances_parent_request_id(monkeypatch):
    captured: List[LLMEvent] = []

    def fake_chat(**kw):
        return make_response(content="ok")

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)

    chain = Chain()

    def hook(event: LLMEvent):
        captured.append(event)
        chain(event)

    client = LeanLLM(
        api_key="sk-test",
        config=LeanLLMConfig(enable_persistence=False),
        post_call_hook=hook,
    )

    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        **chain.kwargs(),
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "more"}],
        **chain.kwargs(),
    )

    assert captured[1].parent_request_id == captured[0].event_id
    assert captured[1].correlation_id == chain.correlation_id
