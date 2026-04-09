from __future__ import annotations

from typing import Any

import litellm


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    **kwargs: Any,
) -> litellm.ModelResponse:
    """Thin wrapper around litellm.completion."""
    return litellm.completion(
        model=model,
        messages=messages,
        api_key=api_key,
        **kwargs,
    )
