# LeanLLM

Lightweight Python wrapper around [LiteLLM](https://github.com/BerriAI/litellm) with built-in usage tracking and label support.

## Installation

```bash
pip install leanllm
```

Or install locally for development:

```bash
pip install -e .
```

## Quickstart

```python
from leanllm import LeanLLM

client = LeanLLM(api_key="sk-...")

response = client.chat(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    labels={"team": "backend", "feature": "onboarding"},
)

print(response.choices[0].message.content)
```

## Labels

Every request accepts an optional `labels` dict. Labels are attached to the usage event logged for that call, making it easy to slice costs and latency by team, feature, environment, or any dimension you define.

## Usage logs

Each call appends a JSON line to `llm_logs.json` (configurable via `LEANLLM_LOG_FILE` / `LEANLLM_LOG_DIR` env vars):

```json
{"model": "gpt-4o-mini", "prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "latency_ms": 432.1, "labels": {"team": "backend"}}
```
