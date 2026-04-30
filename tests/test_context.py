from __future__ import annotations

import asyncio
import threading
from typing import List

import pytest

from leanllm import LeanLLM, LeanLLMConfig, LLMEvent
from leanllm.context import (
    LeanLLMContext,
    clear_current_context,
    get_current_context,
    set_global_context,
    trace,
    use_context,
)
from tests.conftest import make_response


# ----------------------------------------------------------------------
# Pure label/merge helpers
# ----------------------------------------------------------------------


def test_merged_labels_empty_when_all_fields_none():
    ctx = LeanLLMContext()
    assert ctx.merged_labels() == {}


def test_merged_labels_includes_typed_fields_and_custom_tags():
    ctx = LeanLLMContext(
        user_id="u1",
        session_id="s1",
        feature="onboarding",
        environment="prod",
        custom_tags={"team": "backend"},
    )
    out = ctx.merged_labels()
    assert out == {
        "user_id": "u1",
        "session_id": "s1",
        "feature": "onboarding",
        "environment": "prod",
        "team": "backend",
    }


def test_merged_labels_extra_overrides_custom_tags():
    ctx = LeanLLMContext(custom_tags={"team": "backend"})
    out = ctx.merged_labels(extra={"team": "frontend"})
    assert out["team"] == "frontend"


def test_merge_other_non_none_fields_win_and_custom_tags_unioned():
    base = LeanLLMContext(user_id="u1", custom_tags={"a": "1", "b": "2"})
    other = LeanLLMContext(
        user_id="u2", session_id="s2", custom_tags={"b": "X", "c": "3"}
    )
    merged = base.merge(other=other)
    assert merged.user_id == "u2"
    assert merged.session_id == "s2"
    assert merged.custom_tags == {"a": "1", "b": "X", "c": "3"}


def test_merge_keeps_base_when_other_field_is_none():
    base = LeanLLMContext(user_id="u1", feature="onboarding")
    other = LeanLLMContext(session_id="s2")
    merged = base.merge(other=other)
    assert merged.user_id == "u1"
    assert merged.feature == "onboarding"


# ----------------------------------------------------------------------
# Global / scoped context
# ----------------------------------------------------------------------


def test_set_and_get_global_context():
    set_global_context(context=LeanLLMContext(user_id="u-42"))
    current = get_current_context()
    assert current is not None and current.user_id == "u-42"


def test_clear_current_context_resets_to_none():
    set_global_context(context=LeanLLMContext(user_id="u-42"))
    clear_current_context()
    assert get_current_context() is None


def test_use_context_overrides_within_block_and_restores_on_exit():
    set_global_context(context=LeanLLMContext(user_id="outer"))
    with use_context(context=LeanLLMContext(user_id="inner")):
        current = get_current_context()
        assert current is not None and current.user_id == "inner"
    after = get_current_context()
    assert after is not None and after.user_id == "outer"


def test_use_context_with_no_ambient_sets_then_clears():
    with use_context(context=LeanLLMContext(user_id="solo")):
        current = get_current_context()
        assert current is not None and current.user_id == "solo"
    assert get_current_context() is None


def test_use_context_restores_even_when_body_raises():
    set_global_context(context=LeanLLMContext(user_id="outer"))
    with pytest.raises(RuntimeError):
        with use_context(context=LeanLLMContext(user_id="inner")):
            raise RuntimeError("boom")
    after = get_current_context()
    assert after is not None and after.user_id == "outer"


# ----------------------------------------------------------------------
# trace() — correlation propagation
# ----------------------------------------------------------------------


def test_trace_generates_uuid_when_no_correlation_id():
    with trace() as ctx:
        assert ctx.correlation_id is not None
        assert len(ctx.correlation_id) >= 8


def test_trace_uses_explicit_correlation_id():
    with trace(correlation_id="my-corr") as ctx:
        assert ctx.correlation_id == "my-corr"


def test_trace_preserves_other_fields_via_model_copy():
    set_global_context(context=LeanLLMContext(user_id="u1", feature="onb"))
    with trace(correlation_id="c1") as ctx:
        assert ctx.user_id == "u1"
        assert ctx.feature == "onb"
        assert ctx.correlation_id == "c1"


def test_nested_trace_inner_overrides_then_outer_restored():
    with trace(correlation_id="outer") as outer_ctx:
        assert outer_ctx.correlation_id == "outer"
        with trace(correlation_id="inner") as inner_ctx:
            assert inner_ctx.correlation_id == "inner"
        # back to outer
        again = get_current_context()
        assert again is not None and again.correlation_id == "outer"


def test_trace_inherits_existing_correlation_when_arg_omitted():
    with trace(correlation_id="C1"):
        with trace() as inner:
            assert inner.correlation_id == "C1"


# ----------------------------------------------------------------------
# Thread / async isolation
# ----------------------------------------------------------------------


def test_threading_thread_does_not_inherit_contextvar():
    set_global_context(context=LeanLLMContext(user_id="parent-thread"))
    seen = []

    def child():
        seen.append(get_current_context())

    t = threading.Thread(target=child)
    t.start()
    t.join()
    assert seen == [None]


def test_asyncio_tasks_inherit_trace_correlation_id():
    async def runner():
        with trace(correlation_id="shared"):
            seen: List[str] = []

            async def task():
                ctx = get_current_context()
                seen.append(ctx.correlation_id if ctx is not None else "")

            await asyncio.gather(task(), task(), task())
            return seen

    result = asyncio.run(runner())
    assert result == ["shared", "shared", "shared"]


# ----------------------------------------------------------------------
# Client integration — context flows into LLMEvent
# ----------------------------------------------------------------------


def _make_noop_client(**kwargs) -> LeanLLM:
    return LeanLLM(
        api_key="sk-test", config=LeanLLMConfig(enable_persistence=False), **kwargs
    )


def test_chat_consumes_ambient_context_for_correlation_and_parent(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)

    set_global_context(
        context=LeanLLMContext(correlation_id="C1", parent_request_id="P1")
    )
    client.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert events[0].correlation_id == "C1"
    assert events[0].parent_request_id == "P1"


def test_explicit_kwargs_override_ambient_context(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)

    set_global_context(context=LeanLLMContext(correlation_id="C1"))
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        correlation_id="OVERRIDE",
    )
    assert events[0].correlation_id == "OVERRIDE"


def test_ambient_labels_merged_with_call_labels(monkeypatch):
    events: List[LLMEvent] = []
    monkeypatch.setattr("leanllm.client.chat_completion", lambda **kw: make_response())
    client = _make_noop_client(post_call_hook=events.append)

    set_global_context(
        context=LeanLLMContext(user_id="u1", custom_tags={"team": "backend"})
    )
    client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "x"}],
        labels={"feature": "search"},
    )
    labels = events[0].labels
    assert labels["user_id"] == "u1"
    assert labels["team"] == "backend"
    assert labels["feature"] == "search"
