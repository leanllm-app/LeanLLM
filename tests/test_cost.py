from __future__ import annotations

import builtins


from leanllm.events.cost import CostCalculator, estimate_tokens, extract_provider


# ----------------------------------------------------------------------
# CostCalculator.calculate
# ----------------------------------------------------------------------


def test_calculate_uses_exact_pricing_for_known_model():
    calc = CostCalculator()
    # gpt-4o pricing: 2.50 input, 10.00 output per 1M
    cost = calc.calculate("gpt-4o", 1_000_000, 0)
    assert cost == 2.50


def test_calculate_strips_provider_prefix():
    calc = CostCalculator()
    cost = calc.calculate("openai/gpt-4o", 1_000_000, 0)
    assert cost == 2.50


def test_calculate_versioned_name_resolves_via_prefix_match():
    calc = CostCalculator()
    cost = calc.calculate("gpt-4o-2024-08-06", 1_000_000, 0)
    assert cost == 2.50


def test_calculate_unknown_model_returns_zero():
    calc = CostCalculator()
    assert calc.calculate("unknown-model-xyz", 1000, 1000) == 0.0


def test_calculate_custom_pricing_overrides_and_adds():
    calc = CostCalculator(custom_pricing={"my-model": (1.0, 2.0), "gpt-4o": (0.0, 0.0)})
    assert calc.calculate("my-model", 1_000_000, 1_000_000) == 3.0
    # custom overrides built-in
    assert calc.calculate("gpt-4o", 1_000_000, 1_000_000) == 0.0


def test_calculate_rounds_to_eight_decimals():
    calc = CostCalculator()
    cost = calc.calculate("gpt-4o-mini", 1, 1)
    # raw: (1 * 0.15 + 1 * 0.60) / 1_000_000 = 7.5e-7
    assert cost == round(7.5e-7, 8)


# ----------------------------------------------------------------------
# extract_provider
# ----------------------------------------------------------------------


def test_extract_provider_explicit_prefix():
    assert extract_provider("anthropic/claude-3-5-sonnet-20241022") == "anthropic"


def test_extract_provider_inferred_from_base_name():
    assert extract_provider("gpt-4o-mini") == "openai"
    assert extract_provider("claude-3-5-sonnet-20241022") == "anthropic"
    assert extract_provider("gemini-1.5-pro") == "google"
    assert extract_provider("mistral-large-latest") == "mistral"
    assert extract_provider("command-r") == "cohere"


def test_extract_provider_unknown_prefix_returns_unknown():
    assert extract_provider("totally-fake-llm-9000") == "unknown"


# ----------------------------------------------------------------------
# estimate_tokens
# ----------------------------------------------------------------------


def test_estimate_tokens_returns_positive_int_for_non_empty_text():
    out = estimate_tokens("hello world")
    assert isinstance(out, int) and out > 0


def test_estimate_tokens_minimum_one_for_empty_text(monkeypatch):
    # Force the fallback branch by hiding tiktoken
    real_import = builtins.__import__

    def stub_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub_import)
    assert estimate_tokens("") >= 1


def test_estimate_tokens_falls_back_to_len_div_4_when_tiktoken_missing(monkeypatch):
    real_import = builtins.__import__

    def stub_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub_import)
    text = "x" * 40
    assert estimate_tokens(text) == 10
