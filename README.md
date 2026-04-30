# LeanLLM

Observability layer for LLM calls. Capture every request, replay any of them, never block the request path.

[![CI](https://github.com/Gab-r-x/LeanLLM/actions/workflows/ci.yml/badge.svg)](https://github.com/Gab-r-x/LeanLLM/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/leanllm-ai.svg)](https://pypi.org/project/leanllm-ai/)
[![Python](https://img.shields.io/pypi/pyversions/leanllm-ai.svg)](https://pypi.org/project/leanllm-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

LeanLLM is a thin wrapper around [LiteLLM](https://github.com/BerriAI/litellm) that records every LLM call as a structured event (prompt, response, tokens, cost, latency, lineage), persists it through an async pipeline that never blocks your request, and lets you replay any stored event deterministically.

It works with any provider LiteLLM supports (OpenAI, Anthropic, Mistral, Bedrock, Vertex, etc.).

## Install

```bash
pip install leanllm-ai
```

Optional extras enable backends:

```bash
pip install "leanllm-ai[sqlite]"     # local SQLite store (aiosqlite)
pip install "leanllm-ai[postgres]"   # Postgres store (asyncpg + alembic)
pip install "leanllm-ai[remote]"     # remote SaaS store (httpx)
pip install "leanllm-ai[dev]"        # all of the above + pytest + ruff
```

Requires Python 3.10+.

## 60-second example

```python
import asyncio
from leanllm import LeanLLM, LeanLLMConfig

client = LeanLLM(
    api_key="sk-...",  # your provider key (OpenAI, Anthropic, etc.)
    config=LeanLLMConfig(
        database_url="sqlite:///leanllm_events.db",
        capture_content=True,
        last_event_buffer=32,
    ),
)

response = client.chat(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hi in one word."}],
    labels={"team": "backend", "feature": "demo"},
)
print(response.choices[0].message.content)

# Inspect the event that was just captured (in-memory ring buffer).
event = client.last_event
print(event.event_id, event.total_tokens, event.cost)

# Query persisted events (async, runs on the worker loop).
async def main() -> None:
    events = await client.list_events(limit=5)
    for e in events:
        print(e.event_id, e.model, e.latency_ms, "ms")

asyncio.run(main())
```

The `chat()` call returns immediately with the LiteLLM response. Event capture, persistence, retries, and migrations all happen on a background worker thread — your request path is never blocked.

## Why LeanLLM

- **Transparent capture.** Every call produces an `LLMEvent` with prompt, response, parameters, tokens, cost, latency, lineage, labels.
- **Never blocks.** The chat path enqueues the event and returns; the worker batches and persists asynchronously.
- **Bring your own backend.** SQLite for local dev, Postgres for self-hosted, or push to the LeanLLM Service. Mutually exclusive.
- **No vendor lock-in.** Provider calls go through LiteLLM. Drop LeanLLM and your code still works.
- **Replay any event.** Stored events can be re-executed deterministically with parameter overrides.

## Features

| Feature | Docs |
|---|---|
| Request interception (sync + streaming) | [interception](https://leanllm.dev/docs/leanllm/interception) |
| Context propagation (correlation, parent, labels) | [context](https://leanllm.dev/docs/leanllm/context) |
| Semantic normalization | [normalization](https://leanllm.dev/docs/leanllm/normalization) |
| Deterministic replay | [replay](https://leanllm.dev/docs/leanllm/replay) |
| Lineage & execution graph | [lineage](https://leanllm.dev/docs/leanllm/lineage) |
| Cost & token estimation | [cost](https://leanllm.dev/docs/leanllm/cost) |
| Privacy & redaction modes | [redaction](https://leanllm.dev/docs/leanllm/redaction) |
| Storage query API (`get_event`, `list_events`, `count_events`) | [storage-query](https://leanllm.dev/docs/leanllm/storage-query) |
| CLI (`leanllm logs`, `leanllm replay`) | [cli](https://leanllm.dev/docs/leanllm/cli) |
| Runtime toggles & sampling | [runtime-toggles](https://leanllm.dev/docs/leanllm/runtime-toggles) |
| DX helpers (`init`, `last_event`, `trace`, auto-chain) | [dx-helpers](https://leanllm.dev/docs/leanllm/dx-helpers) |

## Configuration

Configuration is driven by `LeanLLMConfig` (Pydantic) or environment variables. Most-used knobs:

| Field | Env var | Default | What it does |
|---|---|---|---|
| `database_url` | `LEANLLM_DATABASE_URL` | `None` | `postgresql://...` or `sqlite:///...`. Self-hosted persistence. |
| `leanllm_api_key` | `LEANLLM_API_KEY` | `None` | Push events to the LeanLLM Service. Mutually exclusive with `database_url`. |
| `capture_content` | `LEANLLM_CAPTURE_CONTENT` | `false` | Store prompt/response text (subject to `redaction_mode`). |
| `redaction_mode` | `LEANLLM_REDACTION_MODE` | `metadata` | `metadata` / `hashed` / `full`. |
| `sampling_rate` | `LEANLLM_SAMPLING_RATE` | `1.0` | Producer-side sampling (0.0–1.0). Errors always emit. |
| `auto_normalize` | `LEANLLM_AUTO_NORMALIZE` | `false` | Populate `normalized_input` / `normalized_output`. |
| `auto_chain` | `LEANLLM_AUTO_CHAIN` | `false` | Auto-fill `parent_request_id` with the previous event in the same async task. |
| `batch_size` | `LEANLLM_BATCH_SIZE` | `100` | Worker flush trigger (events). |
| `flush_interval_ms` | `LEANLLM_FLUSH_INTERVAL_MS` | `180000` | Worker flush trigger (ms). |
| `debug` | `LEANLLM_DEBUG` | `false` | DEBUG logs + per-event stderr summary. |

Setting both `LEANLLM_DATABASE_URL` and `LEANLLM_API_KEY` raises at startup. Setting neither disables persistence (the SDK still works; events are kept only in `client.last_event` / `client.recent_events()`).

Full reference: [configuration](https://leanllm.dev/docs/leanllm/configuration).

## Backends

| Backend | Trigger | Extra |
|---|---|---|
| `PostgresEventStore` | `LEANLLM_DATABASE_URL=postgresql://...` | `[postgres]` |
| `SQLiteEventStore` | `LEANLLM_DATABASE_URL=sqlite:///...` | `[sqlite]` |
| `RemoteEventStore` | `LEANLLM_API_KEY=lllm_...` | `[remote]` |

All backends share the same flush policy (batch of 100 or 3 minutes, whichever first), the same in-memory retry budget (5 attempts / 30 s), and the same drop-on-overflow guarantee — no local-disk fallback in ephemeral containers.

## Replay

```python
import asyncio
from leanllm import LeanLLM, LeanLLMConfig, ReplayEngine, ReplayOverrides

client = LeanLLM(
    api_key="sk-...",
    config=LeanLLMConfig(database_url="sqlite:///leanllm_events.db"),
)
engine = ReplayEngine(client=client)

async def main() -> None:
    result = await engine.replay_by_id(
        event_id="e8e7...-original-event-id",
        overrides=ReplayOverrides(parameters={"temperature": 0.0}),
    )
    print(result.summary())
    print("text identical:", result.text_identical)

asyncio.run(main())
```

## CLI

The package installs a `leanllm` console script:

```bash
leanllm logs --limit 20
leanllm logs --correlation-id req-abc --errors-only
leanllm replay <event_id> --temperature 0.0
```

The CLI reads the same `LEANLLM_DATABASE_URL` your application uses.

## Development

```bash
git clone https://github.com/Gab-r-x/LeanLLM.git
cd LeanLLM/leanllm_lib
python -m venv venv
source venv/bin/activate
pip install -e ".[dev,sqlite,postgres,remote]"

pytest tests/ -v
ruff check leanllm/ tests/
ruff format --check leanllm/ tests/
```

See [CLAUDE.md](CLAUDE.md) for the full set of contributor rules (testing checklist, commit conventions, backend standards).

## Contributing

Issues and pull requests are welcome at https://github.com/Gab-r-x/LeanLLM. Please:

- branch from `origin/main` (`<type>/<short-description>`),
- run `pytest tests/ -v` and `ruff check` before pushing,
- update tests for every behavior change.

## License

MIT — see [LICENSE](LICENSE).
