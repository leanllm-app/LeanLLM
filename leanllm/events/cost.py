from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# (input_usd_per_1M_tokens, output_usd_per_1M_tokens)
_PRICING: Dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-4o":               (2.50,  10.00),
    "gpt-4o-mini":          (0.15,   0.60),
    "gpt-4-turbo":          (10.00, 30.00),
    "gpt-4":                (30.00, 60.00),
    "gpt-3.5-turbo":        (0.50,   1.50),
    "o1":                   (15.00, 60.00),
    "o1-mini":              (3.00,  12.00),
    "o3-mini":              (1.10,   4.40),
    # Anthropic
    "claude-3-5-sonnet-20241022": (3.00,  15.00),
    "claude-3-5-haiku-20241022":  (0.80,   4.00),
    "claude-3-opus-20240229":     (15.00, 75.00),
    "claude-3-sonnet-20240229":   (3.00,  15.00),
    "claude-3-haiku-20240307":    (0.25,   1.25),
    "claude-sonnet-4-5":          (3.00,  15.00),
    "claude-opus-4-6":            (15.00, 75.00),
    "claude-haiku-4-5":           (0.80,   4.00),
    # Google
    "gemini-1.5-pro":       (1.25,  5.00),
    "gemini-1.5-flash":     (0.075, 0.30),
    "gemini-2.0-flash":     (0.10,  0.40),
    # Mistral
    "mistral-large-latest": (2.00,  6.00),
    "mistral-small-latest": (0.20,  0.60),
}


class CostCalculator:
    """Calculates USD cost from token counts using a provider pricing table."""

    def __init__(self, custom_pricing: Optional[Dict[str, Tuple[float, float]]] = None):
        self._pricing = {**_PRICING, **(custom_pricing or {})}

    def calculate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        key = self._resolve(model)
        if key is None:
            return 0.0
        input_price, output_price = self._pricing[key]
        return round(
            (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
            8,
        )

    def _resolve(self, model: str) -> Optional[str]:
        # 1. exact match
        if model in self._pricing:
            return model
        # 2. strip provider prefix: "openai/gpt-4o" → "gpt-4o"
        base = model.split("/")[-1]
        if base in self._pricing:
            return base
        # 3. prefix match for versioned names: "gpt-4o-2024-08-06" → "gpt-4o"
        for key in self._pricing:
            if base.startswith(key):
                return key
        logger.debug("[LeanLLM] No pricing for '%s', cost=0.0", model)
        return None


def extract_provider(model: str) -> str:
    """Infer the provider name from a litellm model string."""
    if "/" in model:
        prefix = model.split("/")[0]
        known = {"openai", "anthropic", "google", "mistral", "cohere",
                 "azure", "bedrock", "vertex_ai", "huggingface"}
        if prefix in known:
            return prefix
    base = model.split("/")[-1].lower()
    if base.startswith(("gpt-", "o1", "o3", "text-davinci")):
        return "openai"
    if base.startswith("claude"):
        return "anthropic"
    if base.startswith("gemini"):
        return "google"
    if base.startswith("mistral") or base.startswith("mixtral"):
        return "mistral"
    if base.startswith("command"):
        return "cohere"
    return "unknown"


def estimate_tokens(text: str, model: str = "gpt-4o") -> int:
    """Best-effort token count when the provider does not return usage."""
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model.split("/")[-1])
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)
