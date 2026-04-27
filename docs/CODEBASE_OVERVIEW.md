# Codebase Overview — LeanLLM SDK (lib)

> Open-source Python library for capturing LLM usage events with zero latency impact.
> Published as `leanllm-ai` on PyPI.

---

## Architecture

```
User App
  ↓
LeanLLM.chat() / .completion()
  ↓  (sync, non-blocking)
LiteLLM proxy call
  ↓
LLMEvent built (model, tokens, cost, latency, labels)
  ↓
queue.Queue.put_nowait()   ← ~microseconds, never blocks
  ↓
Background worker (daemon thread, asyncio loop)
  ↓  batch flush: 100 events OR 3 minutes
  ├── PostgresEventStore  → asyncpg batch INSERT
  ├── SQLiteEventStore    → aiosqlite batch INSERT
  └── RemoteEventStore    → httpx POST /v1/events (to LeanLLM Service)
```

**Key invariant:** The LLM call path (`chat()` → LiteLLM → enqueue) never touches I/O.
All persistence happens in a separate thread.

---

## Directory Structure

```
leanllm/
├── __init__.py             # Public exports: LeanLLM, LeanLLMConfig, LLMEvent
├── client.py               # LeanLLM class — public API
├── config.py               # LeanLLMConfig (Pydantic, env-driven)
├── proxy.py                # Thin wrapper around litellm.completion()
├── cli.py                  # CLI entry point: `leanllm migrate up/down/current/history`
│
├── events/                 # Event pipeline (internal)
│   ├── models.py           # LLMEvent (Pydantic, schema_version=1)
│   ├── cost.py             # CostCalculator, extract_provider(), estimate_tokens()
│   ├── queue.py            # EventQueue (thread-safe queue.Queue wrapper)
│   └── worker.py           # EventWorker (daemon thread + asyncio loop)
│
└── storage/                # Persistence backends (internal)
    ├── base.py             # BaseEventStore (ABC)
    ├── postgres.py         # PostgresEventStore (asyncpg + Alembic auto-migrate)
    ├── sqlite.py           # SQLiteEventStore (aiosqlite, zero-config)
    ├── remote.py           # RemoteEventStore (httpx POST to LeanLLM Service)
    └── migrations/
        ├── runner.py       # Programmatic Alembic wrapper (upgrade, downgrade, current)
        └── postgres/       # Alembic config + migration versions
            ├── env.py
            ├── alembic.ini
            └── versions/
                └── 20260415_0001_initial_schema.py
```

---

## Public API

### LeanLLM (client.py)

The only class users interact with directly.

```python
from leanllm import LeanLLM

client = LeanLLM(api_key="sk-...")

response = client.chat(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
    labels={"feature": "onboarding", "user_id": "u_123"},
)
```

| Method | Description |
|---|---|
| `chat(model, messages, labels, **kwargs)` | Chat completion via LiteLLM. Returns `ModelResponse`. |
| `completion(model, prompt, labels, **kwargs)` | Convenience: wraps prompt into single-message chat. |

### LeanLLMConfig (config.py)

| Field | Env Var | Default | Description |
|---|---|---|---|
| `database_url` | `LEANLLM_DATABASE_URL` | `None` | Postgres/SQLite URL (self-hosted mode) |
| `leanllm_api_key` | `LEANLLM_API_KEY` | `None` | API key for remote mode (sends to service) |
| `endpoint` | `LEANLLM_ENDPOINT` | `https://api.leanllm.dev` | Service URL (remote mode) |
| `batch_size` | `LEANLLM_BATCH_SIZE` | `100` | Flush when queue reaches this count |
| `flush_interval_ms` | `LEANLLM_FLUSH_INTERVAL_MS` | `180000` (3 min) | Flush timeout (whichever triggers first) |
| `queue_max_size` | `LEANLLM_QUEUE_MAX_SIZE` | `10000` | In-memory queue capacity |
| `auto_migrate` | `LEANLLM_AUTO_MIGRATE` | `true` | Run Alembic on Postgres init |
| `capture_content` | `LEANLLM_CAPTURE_CONTENT` | `false` | Store prompt/response text |

**Mutual exclusion:** Setting both `DATABASE_URL` and `API_KEY` raises `ValueError`.

### LLMEvent (events/models.py)

Persisted record. Every LLM call produces one.

| Field | Type | Description |
|---|---|---|
| `event_id` | `str` | UUID, auto-generated |
| `timestamp` | `datetime` | UTC, auto-generated |
| `model` | `str` | e.g. `"gpt-4o-mini"` |
| `provider` | `str` | e.g. `"openai"` (inferred) |
| `input_tokens` | `int` | Prompt tokens (or tiktoken estimate) |
| `output_tokens` | `int` | Completion tokens |
| `total_tokens` | `int` | Sum |
| `cost` | `float` | USD (from pricing table) |
| `latency_ms` | `int` | Wall-clock ms |
| `labels` | `dict[str, str]` | User-supplied metadata |
| `prompt` | `str | None` | Only if `capture_content=True` |
| `response` | `str | None` | Only if `capture_content=True` |
| `metadata` | `dict` | `{"finish_reason": "stop"}` etc. |
| `schema_version` | `int` | Currently `1` |

---

## Two Operating Modes

### Mode 1: Self-hosted (DATABASE_URL)

```bash
export LEANLLM_DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
```

Events go directly into your Postgres (or SQLite) via asyncpg/aiosqlite.
The lib manages the schema via Alembic. No external service needed.

### Mode 2: Remote (API_KEY)

```bash
export LEANLLM_API_KEY=lllm_xxx
export LEANLLM_ENDPOINT=https://api.leanllm.dev  # optional, has default
```

Events are batched and POSTed to the LeanLLM Service.
The service handles persistence, analytics, and dashboard integration.

---

## Storage Backends

All backends implement `BaseEventStore`:

```python
class BaseEventStore(ABC):
    async def initialize(self) -> None: ...
    async def save(self, event: LLMEvent) -> None: ...
    async def save_batch(self, events: list[LLMEvent]) -> None: ...
    async def close(self) -> None: ...
```

| Backend | Module | When to Use |
|---|---|---|
| `PostgresEventStore` | `storage/postgres.py` | Production self-hosted |
| `SQLiteEventStore` | `storage/sqlite.py` | Dev / tests / zero-config |
| `RemoteEventStore` | `storage/remote.py` | SaaS mode (sends to service) |

The `create_store()` factory in `storage/__init__.py` picks the right one
based on config (API_KEY → Remote, DATABASE_URL → Postgres/SQLite).

---

## Event Pipeline Detail

### Queue (events/queue.py)

- `queue.Queue` (stdlib, thread-safe)
- `enqueue()` → `put_nowait()` — never blocks
- If full → drops event, increments counter, logs warning every 100 drops
- `drain(batch_size)` / `drain_all()` for the worker

### Worker (events/worker.py)

- Runs in a **daemon thread** with its own `asyncio.new_event_loop()`
- Flush policy: `batch_size` events OR `flush_interval_ms` — whichever first
- Retry: 3 attempts with exponential backoff (0.5s, 1s, 2s)
- On failure after retries → drops batch, logs error
- On shutdown (`atexit`): drains remaining events, final flush, closes store

### Cost Calculator (events/cost.py)

- `_PRICING` dict: model name → `(input_per_1M, output_per_1M)` in USD
- Resolves versioned names via prefix match: `gpt-4o-2024-08-06` → `gpt-4o`
- `extract_provider(model)`: infers provider from model string
- `estimate_tokens(text, model)`: tiktoken fallback when provider doesn't return usage

---

## CLI

```bash
leanllm migrate up       [--url URL] [--rev head]
leanllm migrate down     [--url URL] [--rev -1]
leanllm migrate current  [--url URL]
leanllm migrate history  [--url URL]
```

Uses `LEANLLM_DATABASE_URL` by default, overridable with `--url`.

---

## Dependencies

### Required (core)
- `litellm` — multi-provider LLM client
- `pydantic` — models + config
- `python-dotenv` — env loading

### Optional (extras)
- `[postgres]` — asyncpg, alembic, sqlalchemy
- `[sqlite]` — aiosqlite
- `[remote]` — httpx
- `[dev]` — all of the above + pytest, ruff
