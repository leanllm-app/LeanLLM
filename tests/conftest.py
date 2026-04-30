from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from leanllm.context import clear_current_context


def pytest_configure(config):
    config.option.asyncio_mode = "auto"


@pytest.fixture(autouse=True)
def _reset_context_var():
    """Ensure each test starts with no ambient LeanLLMContext."""
    clear_current_context()
    yield
    clear_current_context()


def make_response(
    *,
    content: Optional[str] = "ok",
    finish_reason: str = "stop",
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
    tool_calls: Optional[List[Any]] = None,
    no_choices: bool = False,
    no_usage: bool = False,
) -> SimpleNamespace:
    """Build a minimal LiteLLM-shaped ModelResponse fake."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    choices = [] if no_choices else [choice]

    if no_usage:
        usage = None
    else:
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
    return SimpleNamespace(choices=choices, usage=usage)


def make_chunk(
    *,
    delta_content: Optional[str] = None,
    finish_reason: Optional[str] = None,
    tool_calls: Optional[List[Any]] = None,
    usage: Optional[SimpleNamespace] = None,
) -> SimpleNamespace:
    """Build a streaming chunk with `delta` shape."""
    delta = SimpleNamespace(content=delta_content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)
