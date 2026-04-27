# Backend Engineering Standards

Coding standards for this Python library. Every rule below is non-negotiable.

## Table of Contents

1. [Public API & Type Safety](#1-public-api--type-safety)
2. [Keyword-Only Method Signatures](#2-keyword-only-method-signatures)
3. [Error Handling](#3-error-handling)
4. [Units in Attribute Names](#4-units-in-attribute-names)
5. [Nullability Has Meaning](#5-nullability-has-meaning)
6. [Storage / I/O Ownership](#6-storage--io-ownership)
7. [Enums for Status & State](#7-enums-for-status--state)
8. [Try/Except is Not Control Flow](#8-tryexcept-is-not-control-flow)
9. [Clean Code Doesn't Need Comments](#9-clean-code-doesnt-need-comments)
10. [No Dynamic Attribute Access](#10-no-dynamic-attribute-access)
11. [Imports at Top of File](#11-imports-at-top-of-file)
12. [Logging Standards](#12-logging-standards)
13. [Async / Threading Boundaries](#13-async--threading-boundaries)
14. [Schema Versioning](#14-schema-versioning)

---

## 1. Public API & Type Safety

### Core Principle

Use Pydantic models for **logical concepts** and **communication contracts**. Use explicit primitives for everything else.

### When to Use Pydantic

**Use Pydantic when the value:**

1. Crosses a **boundary** the user can see (public API, persisted record, network payload).
2. Is passed through **3+ functions** as a coupled concept.

```python
# Persisted contract — Pydantic
class LLMEvent(BaseModel):
    event_id: str
    timestamp: datetime
    model: str
    ...

# Public API surface — Pydantic for config
class LeanLLMConfig(BaseModel):
    database_url: str | None = None
    batch_size: int = 100
    ...
```

### When to Use Explicit Parameters (NOT Pydantic)

For internal helper functions called in 1-2 places:

```python
# Good — explicit, easy to read at call site
def _build_event(
    *,
    model: str,
    messages: list[dict[str, str]],
    response: ModelResponse,
    labels: dict[str, str],
    latency_ms: int,
) -> LLMEvent:
    ...
```

### Return Types

- **Structured data?** Pydantic.
- **Single primitive?** Primitive (`bool`, `int`, `str`).
- **Never** return raw `dict` / `list` for structured data the caller has to introspect.

### Decision Tree

| Case | Use |
|---|---|
| Public API parameter | Pydantic |
| Persisted record | Pydantic |
| Passed through 3+ functions | Pydantic |
| Internal helper (1-2 callers) | Explicit kwargs |
| Returning multiple fields | Pydantic |
| Returning single value | Primitive |

---

## 2. Keyword-Only Method Signatures

**Rule:**
All function/method parameters (except `self`, `cls`, and dunder methods) MUST be keyword-only via `*`.

**Why:** Forces explicitness at call sites, prevents argument-order bugs, makes refactoring safe.

### Standard Pattern

```python
# Instance method
def chat(self, *, model: str, messages: list, labels: dict | None = None) -> Response:
    ...

# Class method
@classmethod
def from_env(cls) -> "LeanLLMConfig":
    ...

# Standalone function
def estimate_tokens(*, text: str, model: str = "gpt-4o") -> int:
    ...

# Async method
async def save_batch(self, *, events: list[LLMEvent]) -> None:
    ...
```

### Call Site Comparison

```python
# Without keyword-only (bad)
chat("gpt-4o", messages, labels, 30)  # What's 30? Order-dependent!

# With keyword-only (good)
chat(model="gpt-4o", messages=messages, labels=labels, timeout=30)
```

### Exceptions

- `__init__` and other dunder methods may use positional args.
- Single-parameter functions (`def f(x: int)`) don't need `*`.
- Decorators returning the same callable type don't need to enforce this.

---

## 3. Error Handling

### Two Tiers

**Tier 1 — Library boundary (raise exceptions):**
Public API methods raise standard Python exceptions for caller errors:

```python
def __init__(self, *, api_key: str) -> None:
    if not api_key:
        raise ValueError("api_key is required")
```

**Tier 2 — Internal pipeline (never crash the request path):**
Background workers and async operations MUST NOT raise into the user's request path. Catch, log, and continue.

```python
async def _flush_with_retry(self, *, events: list[LLMEvent]) -> bool:
    for attempt in range(max_retries):
        try:
            await self._store.save_batch(events=events)
            return True
        except Exception as exc:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))
            else:
                logger.error("Dropping %d events after retries: %s", len(events), exc)
    return False
```

### Rule

- **Public API:** raise specific, well-named exceptions. Document them.
- **Internal background work:** catch broadly, log with context, never propagate to user code.
- **Never** swallow an exception silently. At minimum: `logger.error(...)`.

### What NOT to do

```python
# Bad — swallows everything
try:
    do_work()
except Exception:
    pass

# Bad — re-raises a generic error losing the cause
try:
    do_work()
except SomeError:
    raise Exception("something failed")

# Good — log and re-raise, or log and continue with context
try:
    do_work()
except SomeError as exc:
    logger.error("do_work failed: context=%s err=%s", ctx, exc)
    raise
```

---

## 4. Units in Attribute Names

**Rule:**
Every field representing a measurement MUST include the unit in the name.

**Format:** `<name>_<unit>`

### Datetime

```python
# Good
created_at_utc: datetime
scheduled_for_utc: datetime

# Bad
created_at: datetime    # Timezone ambiguous
timestamp: datetime     # Timezone ambiguous
```

All datetimes MUST be timezone-aware:

```python
from datetime import datetime, timezone
now = datetime.now(timezone.utc)  # Good
now = datetime.now()              # Bad — naive
```

### Duration

```python
# Good
latency_ms: int
flush_interval_ms: int
cache_ttl_seconds: int

# Bad
latency: int        # Milliseconds? Seconds?
timeout: int        # Ambiguous
```

### Size / Storage

```python
# Good
file_size_bytes: int
queue_max_size: int   # count is implied; "size" alone is OK for counts
chunk_size_bytes: int

# Bad
file_size: int      # Bytes? KB?
```

### Currency

```python
# Good
cost_usd: float                 # USD float for tiny per-token amounts
amount_usd_cents: int           # Integer cents for billable amounts

# Bad
cost: float                     # What currency?
price: int                      # Cents or dollars?
```

### Token Counts

```python
# Good
input_tokens: int
output_tokens: int
total_tokens: int

# Bad
tokens: int                     # Which side?
```

---

## 5. Nullability Has Meaning

### Never Use Sentinel Values

**Forbidden as "no value":**
- `""` (empty string)
- `0` / `0.0`
- `[]` (when absence is the real state)
- `"N/A"`, `"unknown"`, `"null"`

**Use `None`** to mean missing, unknown, or not-yet-available.

```python
# Bad
event.response = ""              # Is this "no response" or "empty response"?
event.cost = 0                   # Cost unknown? Or actually free?

# Good
event.response = None            # Clear: no response captured
event.cost = None                # Clear: cost unknown (then default to 0.0 if needed)
```

### Nullability Must Be Explicit in the Schema

```python
class LLMEvent(BaseModel):
    event_id: str                # Required, never None
    cost: float                  # Required with default at construction time
    prompt: str | None = None    # Explicitly optional
    metadata: dict = Field(default_factory=dict)  # Required, defaults to empty
```

### Lists vs None

- `None` → not yet computed / not applicable.
- `[]` → computed, empty.

```python
event.labels = None     # Avoid — pick a default
event.labels = {}       # Better — explicit "no labels"
```

---

## 6. Storage / I/O Ownership

**Rule:**
I/O lives in dedicated modules behind well-defined interfaces. Business logic does not open connections, sockets, or files.

### The Pattern

- **Storage backends** (`leanllm/storage/*.py`) own all DB I/O.
- **Workers** (`leanllm/events/worker.py`) orchestrate batches; they call store methods, never construct queries.
- **Client** (`leanllm/client.py`) orchestrates the public API; it never touches storage directly.

### Anti-Pattern

```python
# Bad — client opens its own pool
class LeanLLM:
    async def chat(self, ...):
        conn = await asyncpg.connect(self.db_url)  # NO
        await conn.execute("INSERT INTO ...")
```

### Correct

```python
# Good — store owns the pool
class PostgresEventStore(BaseEventStore):
    async def initialize(self) -> None:
        self._pool = await asyncpg.create_pool(self._url)

    async def save_batch(self, events: list[LLMEvent]) -> None:
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT, [_to_row(e) for e in events])
```

### Adding a New Backend

1. Create `leanllm/storage/<name>.py`.
2. Subclass `BaseEventStore` and implement `initialize`, `save`, `save_batch`, `close`.
3. Make heavy dependencies optional via `try/except ImportError` in `initialize`.
4. Wire it through `LeanLLMConfig` (add a new env var if needed).

---

## 7. Enums for Status & State

**Rule:**
Status, kind, type, and state fields MUST be `Enum`, never raw strings.

```python
from enum import Enum

class Provider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    UNKNOWN = "unknown"

class LLMEvent(BaseModel):
    provider: Provider   # Type-safe
```

### Why

```python
# Bad — typo passes silently
event.provider = "openi"
event.provider = "OpenAI"  # Wrong case

# Good — typos fail at runtime + IDE catches them
event.provider = Provider.OPENAI
event.provider = Provider.OPENI  # AttributeError
```

### Querying with Enums

```python
# Good — use .value when serializing
filter_query = {"provider": Provider.OPENAI.value}
```

---

## 8. Try/Except is Not Control Flow

**Rule:**
Don't use `try/except` for expected outcomes. Use explicit conditionals.

### True Exceptions (use try/except)

- External API failures (LLM timeout, network error)
- Database I/O errors
- File system errors
- Library bugs you can't validate against

### Expected Outcomes (use conditionals)

- Optional / missing fields
- Empty lists or dicts
- Lookups that may not find anything

### Examples

```python
# Bad — using exception for control flow
try:
    value = data["key"]
except KeyError:
    value = None

# Good
value = data.get("key")

# Bad
try:
    first = items[0]
except IndexError:
    first = None

# Good
first = items[0] if items else None

# Bad — exception for "not found"
try:
    user = await get_user(user_id)
    return user.email
except UserNotFoundError:
    return None

# Good — explicit
user = await get_user(user_id)
return user.email if user else None
```

### When try/except IS Right

```python
# External call — genuine exception
try:
    response = await litellm.acompletion(model=model, messages=messages, timeout=30)
except litellm.Timeout:
    return Result(success=False, error_code="TIMEOUT")
except litellm.APIConnectionError:
    return Result(success=False, error_code="CONNECTION")
```

---

## 9. Clean Code Doesn't Need Comments

**Principle:**
If you need a comment to explain *what* the code does, the code is unclear. Refactor.

### Bad Comments (explain *what*)

```python
# Calculate the cost
cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
```

→ Just name the function `calculate_cost` and the comment is redundant.

### Good Comments (explain *why*)

```python
# OpenAI prices are quoted per 1M tokens since 2024-Q2 (was per 1k before)
return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

# TODO(#42): switch to streaming when LiteLLM exposes usage in stream events
```

### Acceptable Markers

- `TODO(#issue):` — future work, link to a tracking issue
- `FIXME:` — known bugs
- `HACK:` — temporary workaround, explain why
- `NOTE:` — non-obvious context

### Dead Code

Never comment out code. Delete it. Git remembers.

---

## 10. No Dynamic Attribute Access

**Rule:**
Don't use `getattr`, `setattr`, or `hasattr` for fields you control.

**Why:**
- Type checkers can't see dynamic access.
- Renames silently break.
- Searches (grep, IDE find-references) miss them.

### Bad

```python
# Bad — dynamic access on fields you defined
def update(*, obj, fields: dict):
    for k, v in fields.items():
        setattr(obj, k, v)

# Bad — hasattr to "check support"
if hasattr(client, "stream"):
    client.stream(...)
```

### Good

```python
# Good — explicit
def update(*, obj, email: str | None = None, name: str | None = None):
    if email is not None:
        obj.email = email
    if name is not None:
        obj.name = name

# Good — capability check via interface
if isinstance(client, StreamingClient):
    client.stream(...)
```

### The One Exception

When the field name comes from an **untrusted external source** (webhook payload, third-party API), and you've validated against an allowlist:

```python
ALLOWED_KEYS = {"status", "event_type", "timestamp"}
if key in ALLOWED_KEYS:
    value = payload.get(key)
```

Even then: prefer parsing into a typed Pydantic model first.

---

## 11. Imports at Top of File

**Rule:**
All imports go at the top of the file, ordered per PEP 8:

1. Standard library
2. Third-party
3. Local application

Separate groups with a blank line.

```python
# Standard library
import asyncio
import logging
from datetime import datetime, timezone

# Third-party
from pydantic import BaseModel, Field

# Local
from .config import LeanLLMConfig
from .events.models import LLMEvent
```

### Lazy Imports — Only When Justified

Acceptable cases:
1. **Optional dependency** — import inside `initialize()` and raise a helpful error if missing.

   ```python
   async def initialize(self) -> None:
       try:
           import asyncpg
       except ImportError as exc:
           raise RuntimeError("Install with: pip install leanllm-ai[postgres]") from exc
   ```

2. **Circular import** — refactor instead. If unavoidable, use `TYPE_CHECKING`:

   ```python
   from typing import TYPE_CHECKING
   if TYPE_CHECKING:
       from .events.queue import EventQueue
   ```

Do not use lazy imports for "performance" without measuring.

---

## 12. Logging Standards

### Module Logger

Every module that logs gets its own logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Never use the root logger or `print()` in library code.

### Log Levels

| Level | When |
|---|---|
| `error` | A failure that needs investigation. Even if handled, log at `error` so it surfaces. |
| `warning` | Degraded behavior that recovered (retry succeeded, queue near-full). |
| `info` | Notable lifecycle events (worker started, store initialized, batch flushed). |
| `debug` | Verbose details for development. |

### Required Context

Every error log MUST include:
1. The IDs / inputs that identify the operation.
2. The error itself.

```python
# Good
logger.error("Batch insert failed: batch_size=%d store=%s err=%s",
             len(events), self._store.__class__.__name__, exc)

# Bad — no context
logger.error("Failed to insert")
```

### Format Strings

Prefer `%`-style for the logger so formatting is deferred:

```python
logger.info("Worker started: batch=%d interval=%dms", batch_size, interval_ms)
```

Avoid `f"..."` in hot paths — the string is built even if the level is disabled.

### No Sensitive Data

Never log API keys, prompt content, or response bodies unless `capture_content` (or equivalent) is explicitly enabled by the user.

---

## 13. Async / Threading Boundaries

This library mixes a sync public API with an async background worker. The boundaries matter.

### Rules

1. **The public API is sync** unless explicitly designed otherwise. Users should not need an event loop.
2. **The request path never blocks on I/O.** All persistence happens off-thread.
3. **The worker owns its own event loop** in a daemon thread (`asyncio.new_event_loop()`).
4. **Cross-thread signalling** uses `loop.call_soon_threadsafe()` — never `loop.create_task()` from another thread.
5. **Queues between threads** must be thread-safe (`queue.Queue`, NOT `asyncio.Queue`).

### Adding an Async Public API

If you add async variants (e.g., `AsyncLeanLLM`), they must:

- Live in a separate class. Don't mix sync and async on one object.
- Use `asyncio.Queue` internally (single-loop) instead of `queue.Queue`.
- Document clearly that they require a running event loop.

### Pattern for "Fire and Forget" Work

```python
# In sync code, enqueue a thread-safe object
self._queue.enqueue(event)  # never awaits, never blocks

# In the worker thread's loop, drain and process
events = self._queue.drain(self._batch_size)
await self._store.save_batch(events=events)
```

---

## 14. Schema Versioning

**Rule:**
Persisted records carry an integer `schema_version`. Bump it on every breaking shape change.

```python
class LLMEvent(BaseModel):
    ...
    schema_version: int = 1
```

### When to Bump

- Field renamed, removed, or type changed → **bump**.
- Field added with a sane default → no bump (forward-compatible).
- Semantic change (e.g., units changed) → **bump** even if shape is the same.

### Backward Compatibility

When bumping, write a migration alongside the change:

- For Postgres: an idempotent SQL migration in `leanllm/storage/migrations/` (when introduced).
- For other backends: a backend-specific upgrade path.

Old records must remain readable until a major version explicitly drops them.

---

## Code Review Checklist

Before pushing, verify:

- [ ] All non-dunder methods with 2+ params use keyword-only (`*`)
- [ ] All structured contracts use Pydantic; internal helpers use primitives
- [ ] No raw `dict` / `list` returns for structured data
- [ ] Units present in every measurement field (`_utc`, `_ms`, `_usd`, `_bytes`, etc.)
- [ ] All datetimes are timezone-aware (UTC)
- [ ] Nullability is explicit; no sentinel values
- [ ] Status / state fields use `Enum`
- [ ] No `try/except` for expected outcomes (use conditionals)
- [ ] No `getattr` / `setattr` / `hasattr` on internal fields
- [ ] Imports at top of file
- [ ] Loggers use module name, format with `%`, never log secrets
- [ ] I/O lives in the storage layer; client orchestrates only
- [ ] Background worker exceptions are caught, logged with context, never propagated
- [ ] `schema_version` bumped if persisted shape changed
- [ ] No commented-out code
- [ ] No comments explaining *what* (only *why*)
- [ ] Removed every line that wasn't strictly needed
