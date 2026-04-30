# LeanLLM CORE — TODO.md (Single Source of Truth)

## Context

LeanLLM OSS = client-side library (Python) wrapping LiteLLM calls.

Responsibilities:

* intercept all LLM calls
* structure and normalize data
* enable deterministic replay
* build a reliable data layer for higher-level modules (prompt, eval, cost)

Non-goals:

* no UI
* no heavy optimization logic
* no business decisions
* no advanced eval logic

---

# 0. GLOBAL ARCHITECTURE

[x] Define core modules:
[x] interceptor
[x] context manager
[x] logger
[x] normalizer
[x] replay engine
[x] transport (client → service)
[x] privacy/redaction
[x] config system

[x] Define central object model:
[x] RequestEvent
[x] ResponseEvent
[x] Metadata
[x] TokenUsage
[x] Cost
[x] NormalizedInput
[x] NormalizedOutput

### Implementation Notes

- **Files:** `leanllm/client.py`, `leanllm/config.py`, `leanllm/proxy.py`, `leanllm/events/models.py`, `leanllm/events/cost.py`, `leanllm/events/queue.py`, `leanllm/events/worker.py`, `leanllm/storage/__init__.py`, `leanllm/storage/remote.py`, `leanllm/context.py`, `leanllm/redaction.py`, `leanllm/replay.py`, `leanllm/normalizer.py`.
- **Public entry points:** `LeanLLM`, `LeanLLMConfig`, `LLMEvent` (re-exported from `leanllm/__init__.py`). Everything in `events/*`, `storage/*`, `context.py`, `redaction.py`, `replay.py`, `normalizer.py` is **internal**.
- **Key behaviors / invariants:**
  - `LLMEvent` remains the canonical persisted record. `RequestEvent` (pre-call input snapshot) and `ResponseEvent` (post-call output snapshot) are declared alongside it in `events/models.py` as typed contracts for future refactors — **the client currently builds `LLMEvent` directly**, so those two classes are data shapes, not wired into the pipeline yet.
  - `create_store` factory picks backend by precedence: `api_key` → `RemoteEventStore`, `database_url` → `PostgresEventStore`/`SQLiteEventStore` (scheme-based).
  - Mutual exclusion of `LEANLLM_DATABASE_URL` and `LEANLLM_API_KEY` enforced in `LeanLLMConfig.from_env()` (raises `ValueError`).
  - All sub-modules are implemented:
    - `context.py` — `LeanLLMContext` Pydantic model + `merged_labels()` helper, full ContextVar propagation, `set_global_context`/`use_context`/`trace` (module 2 complete).
    - `redaction.py` — `RedactionMode` enum + `RedactionPolicy` Pydantic model. `apply()` handles all three modes (`FULL`, `METADATA_ONLY`, `REDACTED`) with built-in patterns (email/phone/CPF/SSN) + custom regex (module 9 complete except field-level toggles).
    - `replay.py` — `ReplayEngine`, `ReplayOverrides`, `ReplayResult` fully implemented (single + batch replay, unified diff, token/latency deltas) (module 5 complete).
    - `normalizer.py` — `NormalizedInput`, `NormalizedOutput`, three enums + `canonicalize` / `semantic_hash` / `length_bucket` / `detect_language` / `classify_output` / `classify_input_type` + `normalize_input`/`normalize_output` (module 4 complete).
- **Test hooks / seams:**
  - `create_store(api_key=..., database_url=...)` callable directly with overrides.
  - `LeanLLMConfig` is a Pydantic `BaseModel` — construct with overrides or use `from_env()` after `monkeypatch.setenv(...)`.
  - `LeanLLMContext.merged_labels(extra={...})` produces the label dict to attach to `LLMEvent.labels`.
  - `RedactionPolicy(mode=RedactionMode.REDACTED)` + `apply(...)` exercises full masking pipeline.

### Tests

**Target file(s):** `tests/test_storage_factory.py`, `tests/test_context.py`, `tests/test_replay.py`, `tests/test_normalizer.py`

**Cases to cover:**
- [x] create_store with api_key returns RemoteEventStore (api_key wins over database_url)
- [x] create_store with postgresql URL returns PostgresEventStore
- [x] create_store with sqlite URL returns SQLiteEventStore
- [x] create_store — edge: sqlite:///:memory: routes to SQLiteEventStore
- [x] create_store — error: unsupported URL scheme raises ValueError
- [x] create_store — error: neither api_key nor database_url raises ValueError
- [x] LeanLLMContext.merged_labels returns empty dict when all fields are None
- [x] LeanLLMContext.merged_labels includes typed fields + custom_tags
- [x] LeanLLMContext.merged_labels — edge: extra kwarg overrides custom_tags
- [x] Public surface of `leanllm` package re-exports LeanLLM, LeanLLMConfig, LLMEvent (covered in test_client.py)

---

# 1. REQUEST INTERCEPTION LAYER

[x] Wrap LiteLLM execution entrypoint:
[x] pre-call hook
[x] post-call hook
[x] error hook

[x] Capture request payload:
[x] messages (system/user/assistant)
[x] tools/functions
[x] model
[x] provider
[x] parameters (temperature, max_tokens, etc)

[x] Capture response payload:
[x] final text
[x] tool_calls
[x] finish_reason
[x] usage (tokens if provided)

[x] Streaming support:
[x] capture token chunks
[x] reconstruct final output
[x] measure time_to_first_token
[x] measure total_stream_time

[x] Generate identifiers:
[x] request_id (uuid)
[x] allow override
[x] correlation_id (optional)

### Implementation Notes

- **Files:** `leanllm/client.py` (full rewrite for this module), `leanllm/proxy.py` (unchanged thin wrapper), `leanllm/events/models.py` (new `LLMEvent` fields + `ErrorKind` enum).
- **Public entry points:**
  - `LeanLLM(api_key, config=None, *, pre_call_hook=None, post_call_hook=None, error_hook=None)` — hooks are keyword-only.
  - `LeanLLM.chat(model, messages, labels=None, *, request_id=None, correlation_id=None, parent_request_id=None, **kwargs)`.
  - `LeanLLM.completion(model, prompt, labels=None, *, request_id=None, correlation_id=None, parent_request_id=None, **kwargs)`.
- **Hook semantics (all non-raising — hook exceptions are caught + `logger.exception` only):**
  - `pre_call_hook(snapshot: dict)` fires before the LiteLLM call. Snapshot shape: `{request_id, correlation_id, parent_request_id, model, provider, messages, parameters, tools}`. Observe-only (mutation not honored).
  - `post_call_hook(event: LLMEvent)` fires **only on successful completion** — after the `LLMEvent` is built and enqueued. Does **not** fire on error.
  - `error_hook(exc: Exception, snapshot: dict)` fires on exception. Fires **after** the error `LLMEvent` has been enqueued (so persistence captures errors too).
- **Parameters capture (whitelist):** `_CAPTURED_PARAMETERS` frozenset in `client.py` = `{temperature, max_tokens, top_p, frequency_penalty, presence_penalty, stop, n, seed, response_format, user, logprobs, top_logprobs, stream}`. Anything else in `**kwargs` is forwarded to LiteLLM but **not persisted**.
- **Tools capture:** `kwargs.get("tools") or kwargs.get("functions")` (either shape). Stored in `LLMEvent.tools`.
- **Tool calls capture:** `response.choices[0].message.tool_calls` → serialized via `_tool_call_to_dict(tc)` which prefers `tc.model_dump()`, falls back to `{"raw": repr(tc)}`. Stored in `LLMEvent.tool_calls`.
- **Identifiers:**
  - `request_id` override: pass via `chat(..., request_id="my-id")` — becomes `LLMEvent.event_id`. Defaults to `str(uuid.uuid4())`.
  - `correlation_id`: pass via `chat(..., correlation_id="corr-xyz")`. Stored on the event; **no propagation across nested calls yet** (that's module 2).
  - `parent_request_id`: pass via kwarg. Same pass-through semantics — hierarchy/tree logic is module 6.
- **Streaming (`stream=True`):**
  - `chat()` returns a generator that yields each chunk as it arrives (caller iterates normally).
  - `_wrap_stream` records first-chunk time for `time_to_first_token_ms`, total elapsed for `total_stream_time_ms` (both also mirrored into `latency_ms`).
  - Accumulates text parts from `chunk.choices[0].delta.content` and reconstructs the full response string.
  - Tool-call deltas are collected into `tool_calls_raw` per chunk.
  - `metadata["stream"] = True` on stream events so downstream can distinguish.
  - Event is emitted in the generator's `finally` block — triggers on normal exhaustion, on consumer `.close()`, and on exception during iteration.
  - **Errors during streaming** are caught, re-raised to the caller, but the error `LLMEvent` is still enqueued and `error_hook` fires.
- **Error events (`_emit_error` + `_build_error_event`):**
  - Classification via `_classify_error(exc)` → `ErrorKind` enum by case-insensitive class-name substring match: `"timeout"`, `"ratelimit"`/`"rate_limit"`, `"parse"`/`"json"`/`"validation"`, `"api"`/`"provider"`/`"connection"`, else `UNKNOWN`.
  - Fields populated on error: `error_kind`, `error_message` (= `str(exc)`), `metadata["error_class"]` (= class name), `latency_ms` (elapsed before the raise), tokens/cost = 0.
  - The original exception is re-raised to the caller.
- **Edge cases observed in code:**
  - No choices in response → content/finish_reason/tool_calls stay `None`; token fallback still runs on the input side.
  - Zero `usage` from provider triggers `estimate_tokens` fallback on both input and output (unchanged behavior).
  - `capture_content=False` → `prompt` and `response` both `None` even on streams and errors.
  - Error path: prompt is captured (if configured) so the user can debug what was sent; response is always `None`.
  - Streaming fake/test: if a chunk yields before usage is known, `input_tokens` comes from `estimate_tokens(messages)`; `output_tokens` from the reconstructed text.
- **Thread/async boundaries:**
  - `chat()`, `_chat_stream()`, `_wrap_stream()`, all `_build_*` / `_emit_*` run on the caller's thread.
  - Hooks run inline on the caller's thread too — a slow hook blocks the request path. Document this to users; keep hooks cheap.
  - Only `EventQueue.enqueue` (non-blocking `put_nowait`) crosses into worker territory.
- **Storage impact (not a Module 1 concern — just noting):**
  - Postgres/SQLite `INSERT` statements are column-fixed and do **not** yet persist `correlation_id`, `parent_request_id`, `parameters`, `tools`, `tool_calls`, `error_kind`, `error_message`, `time_to_first_token_ms`, `total_stream_time_ms`. Those fields exist on the Pydantic model and travel over Remote transport (`model_dump`), but are dropped on local backends until module 8 adds a migration.
  - `schema_version` stays at `1` per standard 14 — all new fields are forward-compatible (defaults present).
- **Test hooks / seams:**
  - Mock `leanllm.client.chat_completion` (monkeypatch) — for non-stream return a MagicMock shaped like `ModelResponse`, for stream return a generator of chunk-shaped mocks.
  - Construct `LeanLLM(api_key="x", config=LeanLLMConfig(enable_persistence=False), pre_call_hook=..., post_call_hook=..., error_hook=...)` — no worker starts, hooks still fire.
  - `_classify_error(exc)` is pure; test directly with synthetic exception classes.
  - `_tool_call_to_dict(obj)` is pure; test with dict input, Pydantic-ish object (has `model_dump`), and a bare object (falls back to `{"raw": ...}`).
  - `_CAPTURED_PARAMETERS` is a frozenset constant — easy to assert in-list/not-in-list.

### Tests

**Target file(s):** `tests/test_client.py`

**Cases to cover:**
- [x] non-streaming chat returns the raw LiteLLM response unchanged
- [x] _classify_error maps timeout-class name to ErrorKind.TIMEOUT
- [x] _classify_error maps ratelimit to ErrorKind.RATE_LIMIT
- [x] _classify_error maps json/validation to ErrorKind.PARSING_ERROR
- [x] _classify_error maps api/provider/connection to ErrorKind.PROVIDER_ERROR
- [x] _classify_error default falls back to ErrorKind.UNKNOWN
- [x] _tool_call_to_dict — dict passes through unchanged
- [x] _tool_call_to_dict — object with model_dump delegates to it
- [x] _tool_call_to_dict — edge: bare object falls back to {"raw": repr}
- [x] pre_call_hook receives snapshot with request_id/model/messages
- [x] post_call_hook fires on success with built LLMEvent
- [x] post_call_hook does NOT fire on error
- [x] error_hook fires on exception after error event is enqueued
- [x] request_id override becomes LLMEvent.event_id
- [x] correlation_id kwarg is persisted on the event
- [x] parent_request_id kwarg is persisted on the event
- [x] parameters whitelist captured (temperature/max_tokens/stream) and unknown kwargs dropped
- [x] tools kwarg captured on the event
- [x] tools fallback to "functions" kwarg when tools absent
- [x] tool_calls captured from response.choices[0].message.tool_calls
- [x] streaming: wrapper yields all chunks to the caller
- [x] streaming: ttft_ms and total_stream_time_ms recorded; metadata.stream=True
- [x] streaming — error: exception during iteration emits error event and re-raises
- [x] edge: response with no choices leaves content/finish_reason/tool_calls as None
- [x] edge: missing provider usage triggers estimate_tokens fallback on input and output
- [x] edge: pre_call_hook raising does not break request path
- [x] edge: post_call_hook raising does not break request path
- [x] edge: error_hook raising does not swallow the original exception
- [x] error: chat re-raises LiteLLM exception after enqueuing error event

---

# 2. METADATA & CONTEXT PROPAGATION

[x] Define context object:
[x] user_id
[x] session_id
[x] feature
[x] environment
[x] custom_tags (dict)

[x] Global context:
[x] set once (init)
[x] thread-safe storage

[x] Per-request override:
[x] merge global + local context

[x] Context propagation:
[x] inherit across nested calls
[x] support async flows

[x] Correlation:
[x] group related calls
[x] trace multi-step chains

### Implementation Notes

- **Files:** `leanllm/context.py` (full implementation), `leanllm/client.py:108-152` (context consumption in `chat`), `leanllm/__init__.py` (public exports).
- **Public entry points:** `LeanLLMContext`, `set_global_context(*, context)`, `use_context(*, context)`, `trace(*, correlation_id=None)` — all re-exported from `leanllm`. Internal helpers: `get_current_context()`, `clear_current_context()` (stay under `leanllm.context.*`).
- **Key behaviors / invariants:**
  - Propagation uses `contextvars.ContextVar` (`_current_context`, default `None`) — thread-isolated in sync code, automatic inheritance across `asyncio` tasks via stdlib semantics.
  - `LeanLLMContext` fields: `user_id`, `session_id`, `feature`, `environment`, `custom_tags: Dict[str, str]`, `correlation_id`, `parent_request_id`. All optional; `custom_tags` defaults to `{}`.
  - `merged_labels(extra=...)` flattening order (later keys win): identity fields (`user_id`/`session_id`/`feature`/`environment`) → `custom_tags` → `extra` (per-call `labels`).
  - `merge(other=...)` rule: `other`'s non-`None` scalar fields win; `custom_tags` are **unioned** (`other` wins on key collision).
  - `use_context(context=...)` computes `base.merge(other=context)` if there is an ambient context, else uses the incoming context verbatim, then pushes via `ContextVar.set` + `reset(token)` on exit.
  - `trace(correlation_id=None)` does **not** call `merge`: it copies the ambient context and only overrides the single field `correlation_id`. Resolution order for the id: explicit arg > ambient `correlation_id` > freshly generated `uuid.uuid4()` str.
  - Client integration (`chat` / `completion`): `context` keyword-only kwarg > `get_current_context()` > `None`. When ambient is non-`None`: `effective_correlation = correlation_id kwarg or ambient.correlation_id`; same pattern for `parent_request_id`. Labels passed to the event are `ambient.merged_labels(extra=labels)` when ambient exists, else `labels or {}`.
- **Edge cases observed in code:**
  - `set_global_context` sets the ContextVar on the **current** context only — in sync code that's the caller's thread, in asyncio that's the running task. Calling it from module-level init binds to the main thread/loop.
  - `threading.Thread` does **not** inherit the parent thread's `ContextVar` — child threads start with `default=None`. Users must set it inside the thread target if desired.
  - `asyncio.create_task()` / `asyncio.gather()` **do** inherit (Python stdlib behavior). Verified in smoke test (three concurrent tasks all observed the same `trace()` correlation_id).
  - `use_context` with no ambient context behaves as a direct `set`; exit always restores via the token (even if the body raised).
  - Nested `trace()` calls: inner overrides outer correlation_id for the inner scope; on exit the outer id is restored.
  - `trace()` preserves all other ambient fields via `model_copy(update=...)` — identity/tags still flow.
  - Per-call explicit kwargs (`correlation_id=`, `parent_request_id=`, `labels=`) always beat ambient context.
- **Thread/async boundaries:**
  - `_current_context` is a module-level `ContextVar`. Safe for concurrent reads/writes from the same thread or asyncio task; isolated between threads.
  - Context consumption happens entirely on the caller's thread inside `chat()`. The background worker does not read context — it persists the already-materialized `LLMEvent`.
- **Not yet implemented (referenced by TODO):** none — module 2 is complete.
- **Related work delegated to other modules:**
  - Lineage tree (parent-child edges, execution graph) is Module 6. `parent_request_id` is pass-through only here.
  - Persistence of `correlation_id` / `parent_request_id` / identity-derived labels in Postgres/SQLite is Module 8 (column migration). Remote transport already carries everything via `LLMEvent.model_dump(mode="json")`.
- **Test hooks / seams:**
  - `clear_current_context()` before each test isolates state (ContextVar persists across test functions otherwise).
  - Construct `LeanLLM(api_key="x", config=LeanLLMConfig(enable_persistence=False), post_call_hook=events.append)` to inspect emitted events directly without hitting storage.
  - For thread-isolation tests: spawn a `threading.Thread` and call `get_current_context()` inside its target — expect `None`.
  - For async inheritance tests: run `asyncio.gather(task1, task2)` inside `async def` wrapped by `trace()` — every task sees the same correlation_id.
  - `LeanLLMContext.merge(other=...)` and `LeanLLMContext.merged_labels(extra=...)` are pure — testable without touching the ContextVar.

### Tests

**Target file(s):** `tests/test_context.py`

**Cases to cover:**
- [x] merge: other's non-None scalar fields win; custom_tags unioned
- [x] merge: keeps base when other field is None
- [x] set_global_context + get_current_context round-trip
- [x] clear_current_context resets to None
- [x] use_context overrides within block and restores on exit
- [x] use_context with no ambient sets then clears
- [x] use_context restores even when body raises
- [x] trace generates UUID when no correlation_id given
- [x] trace uses explicit correlation_id arg
- [x] trace preserves identity fields via model_copy
- [x] nested trace: inner overrides then outer restored
- [x] trace inherits existing correlation when arg omitted
- [x] edge: threading.Thread does NOT inherit ContextVar
- [x] edge: asyncio tasks DO inherit trace correlation_id
- [x] client integration: ambient context fills correlation_id + parent_request_id
- [x] client integration: explicit kwargs override ambient
- [x] client integration: ambient labels merged with per-call labels

---

# 3. STRUCTURED LOGGING ENGINE

[x] Define RequestEvent schema:
[x] request_id
[x] parent_request_id
[x] correlation_id
[x] timestamps (start/end)
[x] metadata
[x] raw_input
[x] raw_output
[x] parameters
[x] model/provider
[x] token_usage
[x] cost_estimate
[x] latency
[x] error (optional)

[x] Input/output separation:
[x] raw_input (original)
[x] processed_input (normalized)
[x] raw_output
[x] processed_output

[x] Token tracking:
[x] input_tokens
[x] output_tokens
[x] total_tokens

[x] Latency tracking:
[x] total_latency
[x] time_to_first_token (if streaming)

[x] Error normalization:
[x] timeout
[x] rate_limit
[x] provider_error
[x] parsing_error

### Implementation Notes

- **Files:** `leanllm/events/models.py` (`LLMEvent` + `RequestEvent` + `ResponseEvent` + `Provider` + `ErrorKind`), `leanllm/client.py` (`_build_event_from_response`, `_build_event_from_stream`, `_build_error_event`, `_classify_error`), `leanllm/normalizer.py` (`NormalizedInput`, `NormalizedOutput`).
- **Public entry points:** `LLMEvent` (only Pydantic model exported publicly from `leanllm`). `RequestEvent`/`ResponseEvent`/`ErrorKind` live under `leanllm.events.models` — treated as internal until a consumer needs them.
- **LLMEvent field map (what maps to which TODO item):**
  - `event_id: str` → "request_id" (UUID-v4 default; caller can override via `chat(request_id=...)`).
  - `correlation_id: Optional[str]`, `parent_request_id: Optional[str]` → identifier columns, populated by Module 1 + Module 2 (context).
  - `timestamp: datetime` + `latency_ms: int` → covers "timestamps (start/end)" as a pair of (start instant, duration). `RequestEvent.started_at_utc` / `ResponseEvent.finished_at_utc` exist as typed companions for future wiring.
  - `metadata: Dict[str, Any]` → currently `{"finish_reason": ...}` for non-stream, `{"finish_reason": ..., "stream": True}` for stream, `{"error_class": <ExcClassName>}` for errors.
  - `prompt: Optional[str]` → "raw_input" (JSON-dumped `messages`, gated by `capture_content`).
  - `response: Optional[str]` → "raw_output" (the choice content, gated by `capture_content`).
  - `normalized_input: Optional[NormalizedInput]`, `normalized_output: Optional[NormalizedOutput]` → "processed_input/output" slots. Default `None`; **Module 4 populates**.
  - `parameters: Dict[str, Any]` → whitelist-captured call params (see Module 1 notes).
  - `model: str`, `provider: str` → unchanged.
  - `input_tokens` / `output_tokens` / `total_tokens` → token tracking trio (all `int`).
  - `cost: float` (USD) → cost_estimate.
  - `time_to_first_token_ms: Optional[int]`, `total_stream_time_ms: Optional[int]` → streaming latency pair (populated only on `stream=True` path).
  - `error_kind: Optional[ErrorKind]`, `error_message: Optional[str]` → "error (optional)" column. Populated only on error-path emits.
- **Error normalization (`_classify_error(exc)` in `client.py`):**
  - Case-insensitive substring match on the exception's class name:
    - contains `"timeout"` → `ErrorKind.TIMEOUT`
    - contains `"ratelimit"` or `"rate_limit"` → `ErrorKind.RATE_LIMIT`
    - contains `"parse"` / `"json"` / `"validation"` → `ErrorKind.PARSING_ERROR`
    - contains `"api"` / `"provider"` / `"connection"` → `ErrorKind.PROVIDER_ERROR`
    - anything else → `ErrorKind.UNKNOWN`
  - Order matters (first match wins): `timeout` before `rate_limit` before `parsing` before `provider` before `unknown`.
  - Classification is purely from class name — LiteLLM-specific exception types (e.g. `litellm.Timeout`) are matched via their `__name__`.
- **Edge cases observed in code:**
  - `LLMEvent(..., normalized_input=None, normalized_output=None)` is the default — fields exist in schema even with Module 4 unlanded. `model_dump(mode="json")` serializes them as `null`.
  - Provider can omit `usage` → `input_tokens`/`output_tokens` fall back to `estimate_tokens` (same behavior as before).
  - On stream errors the error `LLMEvent` carries `total_stream_time_ms=None` / `time_to_first_token_ms=None` — the stream-builder isn't invoked when the iterator throws before yielding.
  - `error_kind` is `None` for success events; checking `ev.error_kind is not None` is the canonical "was this an error?" predicate.
  - `schema_version` remains `1` — all additions were forward-compatible per standard 14.
- **Thread/async boundaries:**
  - All event construction runs on the caller's thread inside `chat()`. The worker never builds events — it only persists what it drains.
- **Storage gap (tracked in Module 8):**
  - Postgres/SQLite `INSERT` statements use a fixed column list and do **not yet** persist: `correlation_id`, `parent_request_id`, `parameters`, `tools`, `tool_calls`, `normalized_input`, `normalized_output`, `time_to_first_token_ms`, `total_stream_time_ms`, `error_kind`, `error_message`. These fields exist on the Python model and ship via `RemoteEventStore` (which uses `model_dump(mode="json")`).
- **Not yet implemented (referenced by TODO):** none — all Module 3 items are represented either in code (Module 1-produced fields) or as structural slots (`normalized_input`/`normalized_output` awaiting Module 4's logic).
- **Test hooks / seams:**
  - Construct `LLMEvent(model=..., provider=..., input_tokens=..., output_tokens=..., total_tokens=..., cost=..., latency_ms=...)` with minimal fields — defaults handle the rest.
  - `LLMEvent.model_dump(mode="json")` yields a stable dict suitable for diff-assertions; ISO 8601 timestamp, enum values stringified.
  - `_classify_error(exc)` is pure — test with synthetic exception classes covering each branch + an "unknown" fallback.
  - Error-path events: call a fake `chat_completion` that raises, assert `ev.error_kind`/`ev.error_message`/`ev.metadata["error_class"]`.

### Tests

**Target file(s):** `tests/test_models.py`

**Cases to cover:**
- [x] LLMEvent default event_id is a UUID-shaped non-empty string
- [x] LLMEvent default timestamp is timezone-aware UTC
- [x] LLMEvent default schema_version equals 2
- [x] LLMEvent.model_dump(mode='json') serializes timestamp as ISO 8601 string
- [x] LLMEvent optional fields default to None (prompt, response, tools, tool_calls, error_kind, error_message, time_to_first_token_ms, total_stream_time_ms)
- [x] LLMEvent.labels and metadata default to empty dicts
- [x] ErrorKind enum exposes TIMEOUT/RATE_LIMIT/PROVIDER_ERROR/PARSING_ERROR/UNKNOWN values
- [x] error_kind serializes as string value via model_dump(mode='json')

---

# 4. SEMANTIC NORMALIZATION LAYER

[x] Define NormalizedInput:
[x] intent (optional/manual)
[x] input_type (classification)
[x] language
[x] length_bucket (short/medium/long)

[x] Define NormalizedOutput:
[x] output_type (text/json/code)
[x] structure_detected
[x] length_bucket

[x] Canonicalization:
[x] trim whitespace
[x] normalize casing
[x] remove dynamic tokens (ids, timestamps)

[x] Semantic hashing:
[x] hash normalized input
[x] enable grouping of similar calls

[x] Auto-tagging (basic):
[x] infer missing fields (optional flag)

### Implementation Notes

- **Files:** `leanllm/normalizer.py` (full implementation — pure stdlib: `re`, `hashlib`, `json`), `leanllm/client.py` (`_normalize` helper + integration in `_build_event_from_response` and `_build_event_from_stream`), `leanllm/config.py` (`auto_normalize: bool = False` field + `LEANLLM_AUTO_NORMALIZE` env var).
- **Public entry points (internal module):** `normalize_input(*, messages, auto_tag=False)`, `normalize_output(*, text, auto_tag=False)`, `canonicalize(*, text)`, `semantic_hash(*, text)`, `length_bucket(*, text)`, `detect_language(*, text)`, `classify_output(*, text)`, `classify_input_type(*, messages)`. Types: `NormalizedInput`, `NormalizedOutput`, enums `InputType`, `OutputType`, `LengthBucket`. Nothing re-exported from `leanllm/__init__.py` — callers import from `leanllm.normalizer`.
- **Key behaviors / invariants:**
  - **Canonicalization** (`canonicalize`): applies substitutions in this fixed order on the raw text:
    1. UUID v4 pattern → `<uuid>`
    2. ISO 8601 timestamps (with optional fractional + tz) → `<ts>`
    3. Long hex IDs (`\b[0-9a-f]{16,}\b`) → `<hex>`
    4. Long integers (`\b\d{6,}\b`) → `<num>`
    5. `strip()` + `lower()`
    6. Collapse any whitespace run to a single space
  - **Semantic hash** (`semantic_hash`): `sha256(canonicalize(text).encode("utf-8")).hexdigest()[:16]`. Deterministic and **stable** — two inputs with different dynamic tokens or whitespace/casing but the same semantic content collide into the same hash. 16 hex chars = 64 bits.
  - **Length buckets** (`length_bucket`): word count via `text.split()`. Thresholds: `<= 50` → SHORT, `<= 500` → MEDIUM, else LONG. Empty string → SHORT.
  - **Language detection** (`detect_language`): counts characters across four script regex groups (`latin`, `cjk`, `cyrillic`, `arabic`); returns the dominant one by count, or `None` if all zero or text is empty/whitespace. Not ISO 639-1 — just script family tags.
  - **Output classification** (`classify_output`): priority order is (1) JSON (via `json.loads` on `stripped[0] in "{["`), (2) fenced code blocks (`\`\`\``), (3) default TEXT. Returns `(OutputType, structure_detected)` where `structure_detected` is `"json"` / `"fenced_code"` / `None`.
  - **Input type classification** (`classify_input_type`): returns `TOOL` if any message has `role == "tool"`, else `CHAT`. Empty messages → `UNKNOWN`. Never returns `COMPLETION` — that enum value is reserved for future use (legacy completion endpoints), not auto-inferred.
  - **Auto-tag semantics**: `auto_tag=False` → only deterministic fields populated (`length_bucket` always; `semantic_hash` when text is non-empty). `auto_tag=True` → adds the heuristic/classifier fields (`input_type`, `language`, `output_type`, `structure_detected`). `intent` is **always** `None` — per TODO, it is manual-only.
  - **Client integration** (gated by `config.auto_normalize`, default `False`):
    - `_normalize(messages, content, tool_calls)` on `LeanLLM` returns `(NormalizedInput, NormalizedOutput)` for success events only.
    - When `auto_normalize=False` → both are `None` on every event (backward-compatible default).
    - When `auto_normalize=True` → `normalized_input` built with `auto_tag=True` from the messages.
    - Output branch selection: `content` present → `normalize_output(text=content, auto_tag=True)`; else if `tool_calls` present → `NormalizedOutput(output_type=TOOL_CALL, length_bucket=SHORT)`; else `None`.
    - Error events (`_build_error_event`) do **not** run normalization — `normalized_input`/`normalized_output` stay `None`.
- **Edge cases observed in code:**
  - Empty messages list → `normalize_input` returns bucket SHORT, `semantic_hash=None`, `input_type=UNKNOWN` (always, independent of `auto_tag`).
  - Messages with `content=None` or missing key → silently skipped in `_extract_input_text`.
  - Canonicalization is idempotent: `canonicalize(canonicalize(x)) == canonicalize(x)` for any input.
  - JSON classifier requires stripped text to start with `{` or `[` *and* parse — `"  invalid"`, `"{"`, `'{"a":'` all fall through to TEXT.
  - `detect_language` is case-sensitive to the Unicode blocks; punctuation-only / digit-only text returns `None`.
  - Hex-ID regex deliberately requires `\b[0-9a-f]{16,}\b` — avoids clobbering short hex-looking words; digits-only strings are caught by the 6+ digit number rule instead.
  - `classify_output` JSON branch only attempts `json.loads` when the first non-whitespace char is `{` or `[` — cheap short-circuit avoids parse attempts on normal prose.
  - Code fence detection is substring-based (any `\`\`\`` anywhere) — deliberately permissive, will tag "here's the fix: \`\`\`py…\`\`\`" as CODE.
- **Thread/async boundaries:**
  - All normalizer functions are pure and sync. Called on the caller's thread inside `_build_event_*`. No state, no I/O, safe for any context.
- **Schema impact:**
  - `LLMEvent.normalized_input: Optional[NormalizedInput]` and `LLMEvent.normalized_output: Optional[NormalizedOutput]` — Pydantic nested models. `schema_version` stays at `1` (forward-compat). Postgres/SQLite backends do not persist these fields yet (column migration is Module 8); Remote transport carries them via `model_dump(mode="json")` where enum values serialize as their string values.
- **Not yet implemented (referenced by TODO):** none — module 4 is complete.
- **Deliberately out of scope for this module:**
  - Real NLP language identification (e.g., via `langdetect`, `fasttext`) — only script-family heuristics here. Upgrading is a follow-up if Module 4 ever gets a `[detect]` extra.
  - Intent classification — explicitly marked "optional/manual" in the TODO. Users set `NormalizedInput.intent` themselves.
  - COMPLETION input type inference (reserved enum value) — would need a way to distinguish chat-shaped-but-single-turn vs. legacy completion; TODO doesn't require it.
- **Test hooks / seams:**
  - All helpers (`canonicalize`, `semantic_hash`, `length_bucket`, `detect_language`, `classify_output`, `classify_input_type`) are pure — test directly with string/list inputs.
  - `semantic_hash` stability test: different dynamic tokens + whitespace + casing → same output.
  - End-to-end via `LeanLLM(api_key="x", config=LeanLLMConfig(enable_persistence=False, auto_normalize=True), post_call_hook=events.append)` + monkeypatched `chat_completion` returning a fake `ModelResponse`; assert on `events[0].normalized_input` / `normalized_output`.
  - Tool-call path: set `response.choices[0].message.tool_calls = [mock_with_model_dump]` and `content = None` → assert `normalized_output.output_type == TOOL_CALL`.
  - `LEANLLM_AUTO_NORMALIZE` env var flows through `LeanLLMConfig.from_env()` via the `"true"/"false"` string check (same pattern as other bool envs).

### Tests

**Target file(s):** `tests/test_normalizer.py`

**Cases to cover:**
- [x] canonicalize masks UUID / ISO timestamp / long hex / long numbers
- [x] canonicalize strips, lowers, and collapses whitespace
- [x] canonicalize is idempotent
- [x] semantic_hash is 16 hex chars
- [x] semantic_hash collides for inputs that differ only in dynamic tokens
- [x] semantic_hash collides for inputs that differ only in casing/whitespace
- [x] semantic_hash differs for semantically different inputs
- [x] length_bucket: SHORT for empty / boundary 50 words
- [x] length_bucket: MEDIUM above 50, LONG above 500
- [x] detect_language: latin / cjk / cyrillic / arabic
- [x] edge: detect_language returns None for digits/whitespace only
- [x] classify_output: JSON object, JSON array, code-fence, plain text
- [x] edge: classify_output invalid JSON falls back to text
- [x] classify_input_type: chat vs tool vs unknown(empty)
- [x] normalize_input no auto_tag yields UNKNOWN type, no language
- [x] normalize_input auto_tag fills input_type + language
- [x] normalize_input intent always None
- [x] edge: normalize_input empty messages → SHORT bucket, no semantic_hash
- [x] edge: normalize_input skips messages without content
- [x] normalize_output no auto_tag keeps UNKNOWN type
- [x] normalize_output auto_tag classifies JSON
- [x] client integration: auto_normalize=True populates normalized_input/output
- [x] client integration: auto_normalize=False leaves both fields None
- [x] client integration: tool_calls branch yields normalized_output.output_type=TOOL_CALL

---

# 5. DETERMINISTIC REPLAY ENGINE

[x] Store replay-ready snapshot:
[x] full prompt/messages
[x] parameters
[x] model/provider
[x] tools
[x] metadata

[x] Replay API:
[x] replay(request_id)
[x] replay with overrides:
[x] model
[x] parameters
[x] prompt/messages

[x] Batch replay:
[x] list of request_ids
[x] parallel execution

[x] Replay modes:
[x] exact replay
[x] modified replay (for simulation)

[x] Output comparison:
[x] diff text
[x] diff tokens
[x] diff latency

### Implementation Notes

- **Files:** `leanllm/replay.py` (full implementation), `leanllm/__init__.py` (public exports). No changes to `client.py` — the engine reuses `LeanLLM.chat()` unchanged.
- **Public entry points:** `ReplayEngine(*, client)`, `ReplayEngine.replay(*, event, overrides=None)`, `ReplayEngine.replay_batch(*, events, overrides=None, max_workers=4)`, `ReplayOverrides`, `ReplayResult` — all re-exported from `leanllm`.
- **Snapshot source of truth:** `LLMEvent` itself is the replay snapshot. Relevant fields consumed: `event_id` (= original request_id), `model`, `provider`, `parameters` (whitelist-captured by Module 1), `tools`, `prompt` (JSON-dumped messages, only when `capture_content=True`), `response`, `total_tokens`, `latency_ms`.
- **Key behaviors / invariants:**
  - `replay()` always generates a fresh `new_request_id = uuid.uuid4()` and passes it through `client.chat(request_id=...)` — so the replayed call flows through the normal pipeline (hooks, persistence, normalization) as a distinct event.
  - **Message resolution priority:** `overrides.messages` > `json.loads(event.prompt)` > raise `ValueError`. No `event.prompt` and no override → hard failure with a message that points users to `capture_content=True` or explicit `ReplayOverrides(messages=...)`.
  - **Override priority for model/parameters/tools:** override value (if not `None`) > original event value. `parameters` merges as "all or nothing" — override is a full dict replacement, not a field-level merge.
  - **Stream suppression:** the `stream` key is filtered out of `parameters` before forwarding to `client.chat()`. Replay always runs non-streaming so the comparison has a final response to diff.
  - **Exact vs. modified replay:** identified by whether `overrides` is `None` (exact) or not (modified). Same method handles both — no enum / mode flag.
  - **Diff semantics:** `text_identical = (text_before == text_after)`. When they differ *and* both are non-`None`, `text_diff` is a unified diff (`difflib.unified_diff`, `splitlines(keepends=True)`, labelled `original:<id>` / `replay:<id>`). If either side is `None`, `text_diff` stays `None` (no synthetic "empty vs X" diff).
  - **Token/latency deltas:** `tokens_delta = tokens_after - tokens_before`, `latency_ms_delta = latency_ms_after - latency_ms_before`. Can be negative (faster/fewer) or positive. `tokens_after` comes from `response.usage.total_tokens` (not re-estimated).
  - **Batch semantics:** `ThreadPoolExecutor(max_workers=max_workers)`. Results returned **in the same order** as input events (index mapping). Per-item exceptions are caught → returned as `ReplayResult(error_message=..., new_request_id=None, text_before=..., tokens_before=..., latency_ms_before=...)`. The batch never aborts on a single failure; it logs `warning` with the original `event_id`.
  - **Empty batch shortcut:** `replay_batch(events=[])` returns `[]` without opening a thread pool.
- **Edge cases observed in code:**
  - `prompt` is valid JSON but not a list (e.g., a dict) → `ValueError("prompt is not a messages list")`.
  - `prompt` is non-JSON garbage → `ValueError` chained from `json.JSONDecodeError`.
  - Response with no `choices` or `choices[0].message is None` → `new_text` is `None`; comparison still runs (diff stays `None` because one side is `None`).
  - Response with no `usage` attribute → `new_tokens = 0`; delta becomes `-tokens_before`.
  - Response body identical to original but different tokens/latency (e.g., cache hit vs. cold) → `text_identical=True` but nonzero `tokens_delta`/`latency_ms_delta` — useful signal for perf regressions without text drift.
  - `ReplayOverrides` with all fields `None` behaves identically to passing `overrides=None` (pure exact replay) — per-field fallback always resolves to the event.
  - `ReplayResult.model_dump(mode="json")` serializes cleanly; all fields are JSON-native.
- **Thread/async boundaries:**
  - `ReplayEngine` itself is stateless. `replay()` runs synchronously on the caller's thread.
  - `replay_batch` uses a `ThreadPoolExecutor`. Each worker thread calls `client.chat()` independently — `LeanLLM`'s queue/worker are thread-safe, so concurrent replays through the same client are safe.
  - Each replay triggers one post_call_hook invocation on the client's worker thread (normal event path). If the caller registered a hook specifically to capture replay events, it needs to distinguish the fresh events by `event_id == result.new_request_id`.
- **Not yet implemented (referenced by TODO):** none — module 5 is complete.
- **Deliberately out of scope for this module:**
  - **Fetch-by-id from storage.** `replay()` accepts an `LLMEvent` object, not a raw `request_id`, because current `BaseEventStore` backends (Postgres/SQLite) don't persist every field a faithful snapshot needs yet — that work is tracked in Module 8. Users bringing an in-memory event (from `post_call_hook` or `RemoteEventStore` round-trip) can replay today; a `get_event(request_id)` helper is a natural follow-up once Module 8 adds the missing columns.
  - **Semantic / embedding-based diff.** Only line-level unified diff is produced. A cosine-similarity or token-level diff would be additive, not required by the TODO.
  - **Replay for streaming responses.** Replays always force non-streaming (we need the final text for comparison). Even if the original event came from a `stream=True` path, the replay returns a single consolidated response.
  - **Replay for error events.** If the original event has `error_kind` set (i.e., was an error-path emit), calling `replay(event=...)` still attempts to re-run the call. The comparison will show `text_before=None` → no diff; `tokens_before=0` → delta = `tokens_after`. No special-casing.
- **Test hooks / seams:**
  - Monkeypatch `leanllm.client.chat_completion` to return a `MagicMock` shaped like `ModelResponse` — allows deterministic replay without a real API.
  - Construct `LLMEvent(..., prompt=json.dumps([{"role": ..., "content": ...}]), response="...")` directly — no need to go through `LeanLLM.chat()` first.
  - To test stream stripping: seed an event with `parameters={"stream": True, ...}` and assert the captured `chat_completion(**kw)` kwargs do not contain `stream`.
  - To test batch partial failure: mix valid events with one whose `prompt` is `None` (and no override) — result index 1 has `error_message` populated, others succeed.
  - Async propagation is **not** a concern here (engine is sync); threading parallelism is the axis that matters — use a small `max_workers=2` and a mock with a small artificial delay to force concurrent execution.

### Tests

**Target file(s):** `tests/test_replay.py`

**Cases to cover:**
- [x] replay() runs through client and returns ReplayResult with new request id
- [x] replay() text_identical=True yields no diff
- [x] replay() unified diff appears when texts differ
- [x] overrides.messages take priority over event.prompt
- [x] overrides.model replaces original model
- [x] overrides.parameters fully replace original parameters (no merge)
- [x] stream param is filtered out before forwarding
- [x] error: replay raises when prompt missing AND no override
- [x] error: replay raises when prompt is invalid JSON
- [x] error: replay raises when prompt is not a list
- [x] edge: response with no choices yields text_after=None and no synthetic diff
- [x] edge: ReplayOverrides with all-None fields behaves like overrides=None
- [x] replay_batch empty returns empty list
- [x] replay_batch returns results in input order
- [x] replay_batch — partial failure does not abort the batch

---

# 6. REQUEST LINEAGE & EXECUTION GRAPH

[x] Parent-child tracking:
[x] assign parent_request_id
[x] build tree structure

[x] Chain detection:
[x] group related calls

[x] Tool tracking:
[x] tool_name
[x] arguments
[x] execution_time

[x] Graph representation:
[x] serializable tree
[x] ordered execution steps

[x] Metrics per node:
[x] cost per node
[x] latency per node

### Implementation Notes

- **Files:** `leanllm/lineage.py` (full implementation), `leanllm/__init__.py` (public exports). No changes to `client.py`, `events/models.py`, or storage — the module is post-hoc analysis over existing `LLMEvent` records.
- **Public entry points:** `Chain`, `ExecutionNode`, `ExecutionGraph`, `ToolCallRecord`, `parse_tool_calls(*, raw)`, `build_execution_graphs(*, events)` — all re-exported from `leanllm`.
- **Key behaviors / invariants:**
  - **`parse_tool_calls(raw)`** accepts both OpenAI/LiteLLM shape (`{id, type, function: {name, arguments}}`) and flat shape (`{name, arguments}`). `arguments` can be a dict or a JSON string — both work; unparseable JSON falls back to `{}`. `None`/`[]` input → `[]` output. Non-dict entries in the list are silently skipped.
  - **`ToolCallRecord`** fields: `tool_call_id`, `tool_name` (defaults to `"unknown"` when absent), `arguments: Dict[str, Any]` (defaults `{}`), `execution_time_ms: Optional[int]`, `result: Optional[str]`. `execution_time_ms` is **user-populated** — the library captures the tool *specification* but tools are executed by the user, so timing is attached post-hoc (e.g., `node.tool_calls[i].execution_time_ms = measured_ms`).
  - **`ExecutionNode`** mirrors the relevant fields of `LLMEvent` (event_id, parent/correlation, model/provider, cost, latency_ms, input/output/total tokens) plus `tool_calls: List[ToolCallRecord]`, `children: List[ExecutionNode]`, and the three subtree aggregates (`subtree_cost`, `subtree_latency_ms`, `subtree_tokens`). Subtree metrics are computed once during `build_execution_graphs`; they are **not** live — mutating a node's cost after build doesn't propagate.
  - **`build_execution_graphs(events)`** pipeline:
    1. Bucket events by `correlation_id` (events with `correlation_id=None` share one "None" bucket).
    2. Within each bucket, build a `{event_id: ExecutionNode}` map.
    3. For each event, if `parent_request_id` is set **and** the parent is in the same bucket's map, attach as child; otherwise the node becomes a root. Events whose parent is in a different correlation group are treated as orphans → root (no cross-group edges).
    4. Post-order DFS from each root to compute subtree aggregates.
    5. Returned list is sorted by `correlation_id` ascending, with the `None` group last.
  - **`ExecutionGraph`** methods: `flatten()` and `to_ordered_steps()` both return nodes in **DFS pre-order** (children appended in insertion order). `total_cost()`, `total_latency_ms()`, `total_tokens()` sum the root-level subtree metrics.
  - **`Chain`** is a stateful helper for auto-advancing `parent_request_id`:
    - Constructor: `Chain(*, correlation_id=None)` — generates a fresh `uuid.uuid4()` if absent.
    - `kwargs()` returns `{"correlation_id": ..., "parent_request_id": last_or_None}` for `**`-spread into `client.chat()`.
    - `record(event=...)` or direct `chain(event)` call (implements `__call__`) advances `_last_request_id` to `event.event_id` — **only** if the event's `correlation_id` matches the chain's id or is `None`. Events from other chains are ignored, so a single shared `post_call_hook` doesn't cross-contaminate parallel chains.
    - `reset()` clears `_last_request_id` (useful for chain branching).
    - Intended usage: pass `Chain` as the client's `post_call_hook` for hands-free advancement, or call `chain.record(...)` manually.
- **Edge cases observed in code:**
  - Empty `events` → `build_execution_graphs` returns `[]`.
  - Self-parent (`event.parent_request_id == event.event_id`) would attach an event as its own child, creating an infinite loop during subtree DFS. The code does **not** guard against this — it relies on the producer (Module 1 / `Chain`) to never emit such an event. A future hardening pass could detect cycles.
  - Duplicate `event_id` in the input: the later event overwrites the earlier one in the map; only the last occurrence survives as a node. Tree structure is built from the final map state.
  - Tool_calls serialized as `{"raw": repr(tc)}` by `_tool_call_to_dict` (fallback in `client.py` when `model_dump` isn't available) → `parse_tool_calls` falls through to `tool_name="unknown"` and empty `arguments`. Not an error, just lossy.
  - `Chain.kwargs()` returns `parent_request_id: None` before the first `record()` call — the first LLM call in a chain has no parent, which is correct (it's the root).
  - Orphan events (parent_request_id set but pointing outside the current correlation group) become roots in their own group. They do **not** get matched across groups — correlation_id is the hard boundary.
- **Thread/async boundaries:**
  - `Chain` is **not** thread-safe. A single instance shared across threads can race on `_last_request_id`. For concurrent traces, use one `Chain` per thread (or per `asyncio.Task`).
  - `build_execution_graphs` is pure + sync — safe to call from any context over an immutable event list.
  - `parse_tool_calls` is pure.
- **Not yet implemented (referenced by TODO):** none — module 6 is complete.
- **Deliberately out of scope for this module:**
  - **Automatic `execution_time_ms` instrumentation.** We don't wrap tool handlers — users measure their tool execution time themselves and assign to `node.tool_calls[i].execution_time_ms`. Adding an `@record_tool` decorator would be a follow-up, but it conflates concerns (the library doesn't run tools).
  - **Cycle detection** in the tree builder — see "edge cases" above. Producer-side (`Chain` + `chat()`) never generates cycles by construction; post-hoc defence could be added later.
  - **Persisting `ExecutionGraph`** to the event store. The graph is a derived view; re-building from raw events is cheap and avoids schema bloat.
  - **`parent_request_id` auto-injection into `LLMEvent.metadata` for downstream analytics** — not asked.
- **Test hooks / seams:**
  - All graph/tree functions are pure over `LLMEvent` — construct synthetic events with explicit `event_id`/`parent_request_id`/`correlation_id` and assert on node structure. No monkeypatching needed.
  - Chain as hook: `LeanLLM(api_key="x", config=LeanLLMConfig(enable_persistence=False), post_call_hook=chain)` + monkeypatched `chat_completion` → assert each subsequent event's `parent_request_id` equals the previous event's `event_id`.
  - Subtree aggregation: build a tree with distinct costs/latencies/tokens per node and assert on `root.subtree_*` — integer/float arithmetic so exact comparisons are safe.
  - Serialization round-trip: `ExecutionGraph.model_validate(graph.model_dump(mode="json"))` reconstructs correctly (children preserved).
  - `parse_tool_calls` branches: test OpenAI shape with string args, flat shape with dict args, empty input, non-dict elements, and malformed JSON args (should yield empty `{}`).
  - **Do not** test `Chain` thread-safety — it's explicitly documented as single-thread.

### Tests

**Target file(s):** `tests/test_lineage.py`

**Cases to cover:**
- [x] parse_tool_calls OpenAI shape with string args
- [x] parse_tool_calls flat shape with dict args
- [x] parse_tool_calls empty input returns []
- [x] parse_tool_calls skips non-dict entries
- [x] edge: malformed JSON args yield empty {}
- [x] edge: missing name → tool_name="unknown"
- [x] build_execution_graphs empty input returns []
- [x] build_execution_graphs buckets events by correlation_id
- [x] build_execution_graphs attaches children via parent_request_id
- [x] subtree metrics sum root + children (cost / latency_ms / tokens)
- [x] edge: orphan event with external parent becomes root
- [x] edge: None correlation group sorts last
- [x] flatten returns DFS pre-order
- [x] to_ordered_steps is alias of flatten
- [x] total_cost / total_latency_ms / total_tokens sum root subtree metrics
- [x] graph serialization round-trips via model_dump / model_validate
- [x] Chain first call: kwargs() has parent_request_id=None, correlation_id set
- [x] Chain.record advances last_request_id
- [x] Chain.__call__ alias of record
- [x] Chain ignores event from foreign correlation
- [x] Chain accepts event with correlation_id=None
- [x] Chain.reset clears last_request_id
- [x] Chain as post_call_hook auto-advances parent_request_id over two calls

---

# 7. COST & TOKEN ESTIMATION (BASIC CORE ONLY)

[x] Token extraction:
[x] from provider response
[x] fallback estimation (if missing)

[x] Cost calculation:
[x] map model → pricing table
[x] calculate input/output cost

[x] Store:
[x] cost_per_request
[x] tokens_per_request

### Implementation Notes

- **Files:** `leanllm/events/cost.py` (entire file), `leanllm/client.py:135-152` (wiring into event build).
- **Public entry points (internal):** `CostCalculator`, `CostCalculator.calculate(model, input_tokens, output_tokens) -> float`, `extract_provider(model) -> str`, `estimate_tokens(text, model="gpt-4o") -> int`.
- **Key behaviors / invariants:**
  - `_PRICING` is `Dict[model_name, (input_usd_per_1M, output_usd_per_1M)]`. See `cost.py:9-35` for the current table (OpenAI, Anthropic, Google, Mistral families).
  - `CostCalculator(custom_pricing=...)` merges user-provided entries on top of `_PRICING` (custom wins).
  - Resolution order in `_resolve`: (1) exact match, (2) strip `"provider/"` prefix, (3) prefix match against known keys, (4) `None` → cost returns `0.0`.
  - Cost formula: `(input_tokens * in_price + output_tokens * out_price) / 1_000_000`, rounded to 8 decimal places.
  - `extract_provider`: first tries explicit `provider/model` prefix against a known set (`openai, anthropic, google, mistral, cohere, azure, bedrock, vertex_ai, huggingface`); else infers from the base name prefix; else `"unknown"`.
  - `estimate_tokens`: tries `tiktoken.encoding_for_model(base)`, falls back to `cl100k_base`, and finally to `max(1, len(text) // 4)` if tiktoken isn't installed or throws.
- **Edge cases observed in code:**
  - Unknown model → cost is `0.0` (not an error), and a `logger.debug` is emitted.
  - `model` containing `/` is stripped to the base name for pricing lookup (`openai/gpt-4o` → `gpt-4o`).
  - `extract_provider` is case-sensitive on the prefix slot but lowercases the base for keyword checks.
  - `estimate_tokens` clamps to a minimum of 1 for any non-empty text via integer division fallback (empty string returns 1, not 0).
  - `_build_event` calls `estimate_tokens` twice independently — input side reads `messages` content, output side reads `response.choices[0].message.content` (may raise; wrapped in try/except).
- **Thread/async boundaries:**
  - All cost/token code is sync. Called on the request thread inside `_build_event`.
- **Not yet implemented:** nothing in this module. Fully covered.
- **Test hooks / seams:**
  - `CostCalculator(custom_pricing={"my-model": (1.0, 2.0)})` lets tests pin pricing without touching the module-level dict.
  - `_resolve` is prefix-matching — test `"gpt-4o-2024-08-06"` → `"gpt-4o"`.
  - `estimate_tokens` degrades gracefully without tiktoken; tests can monkeypatch `tiktoken` import to force the `len // 4` branch.

### Tests

**Target file(s):** `tests/test_cost.py`

**Cases to cover:**
- [x] CostCalculator.calculate uses exact pricing match for a known model (gpt-4o)
- [x] CostCalculator.calculate strips provider/model prefix before lookup (openai/gpt-4o)
- [x] CostCalculator.calculate — edge: versioned name resolves via prefix match (gpt-4o-2024-08-06 → gpt-4o)
- [x] CostCalculator.calculate — edge: unknown model returns 0.0
- [x] CostCalculator custom_pricing overrides and adds models
- [x] CostCalculator.calculate rounds result to 8 decimal places
- [x] extract_provider picks explicit provider/ prefix when known
- [x] extract_provider infers provider from base-name prefix (gpt-, claude, gemini, mistral, command)
- [x] extract_provider — edge: unknown prefix returns "unknown"
- [x] estimate_tokens returns a positive int for non-empty text
- [x] estimate_tokens — edge: minimum 1 token returned for empty string
- [x] estimate_tokens — edge: tiktoken failure falls back to len//4 heuristic

---

# 8. DATA PIPELINE (CLIENT → SERVICE)

[x] Event batching:
[x] buffer events locally
[x] flush in batches

[x] Retry mechanism:
[x] exponential backoff
[x] retry on network failure

[x] Schema persistence:
[x] Postgres migration for 11 new fields (correlation_id, parent_request_id, parameters, tools, tool_calls, time_to_first_token_ms, total_stream_time_ms, error_kind, error_message, normalized_input, normalized_output)
[x] SQLite schema update (all 11 fields)
[x] Update Postgres INSERT statement to persist all fields
[x] Update SQLite INSERT statement to persist all fields

[ ] Offline mode:
[ ] local persistence (queue)
[ ] flush later

[ ] Payload optimization:
[ ] compression (gzip)
[ ] size limits

[ ] API communication:
[x] send via API_KEY
[ ] include account_id mapping

[ ] Sampling:
[ ] configurable sampling_rate
[ ] drop low-priority events

### Implementation Notes

- **Files:** `leanllm/events/queue.py` (in-memory buffer), `leanllm/events/worker.py` (daemon-thread flusher), `leanllm/storage/remote.py` (HTTP transport), `leanllm/storage/postgres.py:14-40` (INSERT + _to_row with all 25 fields), `leanllm/storage/sqlite.py:15-82` (schema + INSERT with all 25 fields), `leanllm/storage/migrations/postgres/versions/20260427_0002_add_module_fields.py` (Alembic migration).
- **Public entry points (internal):** `EventQueue(max_size=10_000)`, `EventQueue.enqueue/drain/drain_all/empty/dropped`, `EventWorker(queue, store, batch_size, flush_interval_ms)`, `EventWorker.start/stop`, `BaseEventStore.initialize/save/save_batch/close`.
- **Key behaviors / invariants:**
  - **Schema persistence (Module 8 completion):** Postgres migration 0002 adds 11 columns for Module 1-6 fields (correlation_id, parent_request_id, parameters, tools, tool_calls, time_to_first_token_ms, total_stream_time_ms, error_kind, error_message, normalized_input, normalized_output). Both Postgres and SQLite _to_row and INSERT statements updated to persist all fields. RemoteEventStore already includes all fields via `event.model_dump(mode="json")`.
  - **Field serialization:** error_kind serialized as its string value (.value); normalized_input/output serialized via model_dump(mode="json"); other JSON fields serialized via json.dumps; NoneAble fields handled explicitly.
  - **Queue:** `queue.Queue` stdlib wrapper. `enqueue` uses `put_nowait` — drops on full (never blocks). Increments `_dropped`; emits a `logger.warning` every time the drop count hits `_dropped % 100 == 1` (so first drop + every 100th thereafter).
  - **Worker lifecycle:** `start()` spins a daemon thread named `leanllm-worker` that owns its own `asyncio.new_event_loop()`. Registered with `atexit` for graceful shutdown.
  - **Flush policy:** on every tick, `drain(batch_size)` pulls up to `batch_size` events; `_flush_with_retry` then attempts up to 3 tries with backoff `0.5 * 2^attempt` seconds (i.e., 0.5s, 1s, 2s). After max retries, the batch is **dropped** (logged at ERROR, no re-queue).
  - **Timing:** the tick sleeps `flush_interval_ms` *or* wakes early on stop signal via `asyncio.Event.wait()`. Default `flush_interval_ms` in `EventWorker.__init__` is `200ms`, but `LeanLLMConfig.flush_interval_ms` default is `180_000` (3 min) — the config default wins when wired through `LeanLLM.__init__`.
  - **Shutdown:** `stop()` sets the asyncio event via `call_soon_threadsafe`, worker drains remaining events and calls `_store.close()` before exiting the loop.
  - **Remote transport:** `POST {endpoint}/v1/events` with `Authorization: Bearer <api_key>`, `Content-Type: application/json`, body `{"events": [event.model_dump(mode="json"), ...]}`. httpx timeout: `10s` total, `5s` connect. Logs a warning when response body reports `dropped > 0`.
- **Edge cases observed in code:**
  - `save_batch` with empty list or uninitialized client/pool/conn → early return, no-op.
  - If `_store.initialize()` raises, the worker logs and **exits early** — persistence is effectively disabled for the process, but the request path keeps working (events just pile up and eventually drop).
  - `endpoint.rstrip("/")` on construction: trailing slashes in `LEANLLM_ENDPOINT` are tolerated.
  - `Postgres.save_batch` uses `INSERT ... ON CONFLICT (event_id) DO NOTHING` — idempotent retries.
  - `SQLite.save_batch` uses `INSERT OR IGNORE` — same idempotency guarantee.
  - If the optional driver (`aiosqlite`, `asyncpg`, `httpx`) is missing, `initialize()` raises a `RuntimeError` with install hint.
  - **Alembic migration idempotent:** revision 0002 specifies `down_revision = "0001"`; running multiple times is safe (adds columns only if not exists via IF NOT EXISTS, creates indexes only if not exists).
- **Thread/async boundaries:**
  - `EventQueue.enqueue` runs on the request thread (sync). `drain`/`drain_all` run inside the worker's asyncio loop on the daemon thread.
  - Every `BaseEventStore` method is `async def` and must be awaited from the worker loop. Never call from the request path.
- **Not yet implemented (referenced by TODO):**
  - **Offline mode:** `EventQueue` is pure in-memory — no disk spill, no resume. Process crash = lost events.
  - **Compression:** `remote.py` does not set `Content-Encoding: gzip` on outgoing POSTs.
  - **Size limits:** no explicit payload cap; relies on `batch_size` as a proxy.
  - **account_id mapping:** no explicit `account_id` field in the outgoing payload; the service must derive it from the bearer token.
  - **Sampling:** no `sampling_rate` config, no priority labels.
- **Test hooks / seams:**
  - `EventQueue(max_size=small)` lets tests exercise drop behavior deterministically.
  - `BaseEventStore` is abstract — trivial to subclass a `FakeStore` that records `save_batch` calls (e.g., with a `threading.Event` set on receive for deterministic worker tests).
  - `EventWorker(flush_interval_ms=20)` tightens the loop for tests; combined with a fake store + `worker.stop(timeout=2)` for drain assertions.
  - `RemoteEventStore` — use `httpx.MockTransport` or a `respx`-style fixture; the request shape to assert is `POST /v1/events` with body `{"events": [...]}` and bearer header.
  - SQLite `:memory:` is the fast path for store-level integration tests (`database_url="sqlite:///:memory:"`).

### Tests

**Target file(s):** `tests/test_queue.py`, `tests/test_worker.py`, `tests/test_sqlite.py`, `tests/test_remote.py`

**Cases to cover:**
- [x] EventQueue.enqueue returns True when space is available and empty() flips to False
- [x] EventQueue.drain pulls up to batch_size events in FIFO order
- [x] EventQueue.drain_all empties the queue
- [x] EventQueue — edge: enqueue at capacity returns False and increments dropped counter
- [x] EventWorker drains enqueued events into a fake store batch within 2s
- [x] EventWorker — edge: retries failing save_batch up to 3 attempts then drops the batch
- [x] EventWorker flushes remaining events on graceful stop
- [x] SQLiteEventStore._path_from_url: sqlite:///:memory: → :memory:
- [x] SQLiteEventStore._path_from_url: sqlite:////abs/path.db → /abs/path.db
- [x] SQLiteEventStore._path_from_url: sqlite:///./rel.db → ./rel.db
- [x] SQLiteEventStore.save_batch persists an event (round-trip via SELECT)
- [x] SQLiteEventStore — edge: save_batch with empty list or before initialize is a no-op
- [x] SQLiteEventStore — edge: INSERT OR IGNORE makes duplicate event_id idempotent
- [x] RemoteEventStore.save_batch POSTs /v1/events with bearer auth and body {"events": [...]}
- [x] RemoteEventStore — edge: save_batch is a no-op on empty list or before initialize
- [x] RemoteEventStore — edge: service response with dropped>0 emits a warning
- [x] RemoteEventStore — error: HTTP error status raises (worker retry loop handles it)
<!-- pending: offline mode / gzip compression / payload size limits / sampling / account_id mapping not implemented -->

---

# 9. PRIVACY & REDACTION

[x] Built-in redaction:
[x] emails
[x] phone numbers
[x] IDs (CPF/SSN patterns)

[x] Custom rules:
[x] regex-based masking

[x] Modes:
[x] full logging
[x] redacted logging
[x] metadata-only

[ ] Field-level control:
[ ] exclude raw_input
[ ] exclude raw_output

### Implementation Notes

- **Files:** `leanllm/redaction.py` (full implementation with regex patterns), `leanllm/config.py:1-60` (RedactionMode enum import, redaction_mode field, from_env support), `leanllm/client.py:9-24` (import apply_redaction), `leanllm/client.py:414-430` (_capture_content updated to use redaction policy).
- **Key behaviors / invariants:**
  - **RedactionMode enum:** FULL (passthrough), REDACTED (apply masking), METADATA_ONLY (return None).
  - **Built-in patterns:** email regex (RFC-ish), phone (Brazil-format + US), CPF (`\d{3}.\d{3}.\d{3}-\d{2}`), SSN (`\d{3}-\d{2}-\d{4}`). All compiled at module load in _PATTERNS dict.
  - **apply() function:** Returns `None` if input is `None` (all modes). For METADATA_ONLY returns `None`. For FULL returns text unchanged. For REDACTED: applies all enabled built-in patterns (redact_emails, redact_phones, redact_ids all True by default), then applies custom_patterns as compiled regexes (errors silently swallowed if invalid).
  - **Masking tokens:** [EMAIL], [PHONE], [CPF], [SSN], [REDACTED] (for custom).
  - **Integration in client:** `_capture_content` now builds a RedactionPolicy from `config.redaction_mode`, captures prompt (JSON-dumped messages) and response (content) unconditionally, then applies redaction before returning. Replaces the old boolean `capture_content` behavior (backward compat: old code that set capture_content=False will still work because default config.redaction_mode=METADATA_ONLY).
  - **Config field:** `LeanLLMConfig.redaction_mode: RedactionMode = RedactionMode.METADATA_ONLY`. from_env reads `LEANLLM_REDACTION_MODE` (default "metadata"), validates enum value, falls back to METADATA_ONLY on invalid.
- **Edge cases observed in code:**
  - Phone pattern is permissive (detects many Brazil/US variations); may over-match country codes in text like "+55" or area codes alone.
  - Custom pattern compilation errors (invalid regex) are caught and skipped — no exception bubbles to caller.
  - `json.dumps(messages)` can return very large strings; masking is applied post-serialization (not pre), so the regex passes over full JSON.
  - If redaction_mode=METADATA_ONLY, the apply() returns None, so event.prompt and event.response are both None even if messages were passed.
- **Thread/async boundaries:**
  - `apply()` is pure (regex substitution only). Called on the request thread inside `_capture_content`.
  - Pattern compilation happens at module import time, not per-call.
- **Not yet implemented (referenced by TODO):**
  - **Field-level control:** RedactionPolicy has exclude_prompt/exclude_response fields defined but not wired into client._capture_content. Would require separate prompts/responses in capture logic (now unified).
  - **Account-level masking rules:** Currently only global patterns; no user/account-specific custom rules per event.
- **Test hooks / seams:**
  - `apply(policy=RedactionPolicy(mode=RedactionMode.FULL), text=...)` for exact text.
  - `apply(policy=RedactionPolicy(mode=RedactionMode.REDACTED, redact_emails=True, ...), text=...)` for granular control.
  - `apply(policy=RedactionPolicy(custom_patterns=[r'my_pattern']), ...)` for custom regex.
  - Client integration: `LeanLLM(api_key="x", config=LeanLLMConfig(redaction_mode=RedactionMode.REDACTED))` to test full redaction on events.

### Tests

**Target file(s):** `tests/test_redaction.py`, `tests/test_client.py`

**Cases to cover:**
- [x] RedactionPolicy.apply FULL mode returns text unchanged
- [x] RedactionPolicy.apply METADATA_ONLY returns None (capture-less mode)
- [x] RedactionPolicy.apply REDACTED mode masks emails, phones, CPF, SSN with built-in patterns
- [x] RedactionPolicy.apply — edge: None input returns None regardless of mode
- [x] RedactionPolicy.apply — custom patterns applied as compiled regexes, errors swallowed
- [x] config.redaction_mode=METADATA_ONLY → event.prompt/response are None
- [x] config.redaction_mode=FULL → event.prompt/response are captured unmasked
- [x] config.redaction_mode=REDACTED → event.prompt/response are captured then masked (emails→[EMAIL], etc)
- [x] LeanLLMConfig.from_env reads LEANLLM_REDACTION_MODE and validates enum
<!-- pending: field-level exclude_prompt/exclude_response control -->

---

# 10. CONFIGURATION SYSTEM

[ ] Global config:
[x] api_key
[ ] environment
[ ] sampling_rate
[x] redaction_mode

[ ] Runtime config:
[x] enable/disable modules
[ ] toggle replay
[ ] toggle normalization

[ ] Per-request overrides:
[ ] metadata override
[ ] disable logging

[ ] Modes:
[ ] debug mode (verbose)
[ ] production mode (minimal overhead)

### Implementation Notes

- **Files:** `leanllm/config.py` (entire file).
- **Public entry points:** `LeanLLMConfig`, `LeanLLMConfig.from_env()`.
- **Key behaviors / invariants:**
  - All fields are typed Pydantic attributes with defaults. `from_env` reads the `LEANLLM_*` namespace, coerces booleans by `.lower() == "true"`, and ints via `int(...)`.
  - Mutual exclusion: if both `LEANLLM_DATABASE_URL` and `LEANLLM_API_KEY` are set, `from_env` raises `ValueError`.
  - Current boolean/toggle knobs: `enable_persistence`, `auto_migrate`, `capture_content`. Numeric knobs: `queue_max_size`, `batch_size`, `flush_interval_ms`.
  - `endpoint` defaults to `"https://api.leanllm.dev"` but can be overridden both on the model and via `LEANLLM_ENDPOINT`.
  - `load_dotenv()` is called at import time (`config.py:9`) — `.env` is read from CWD on module import.
- **Edge cases observed in code:**
  - Any env var set to a non-`"true"` string (e.g., `"True"` is accepted because of `.lower()`; `"yes"`/`"1"` are **not** accepted and become `False`).
  - Int coercion has no bounds check — negative or huge values will be accepted by the constructor.
  - `enable_persistence=True` but neither URL/API key set → `LeanLLM.__init__` logs an info message and operates with `self._queue = None`, making `_emit` a no-op (`client.py:62-66`, `client.py:119-120`).
- **Not yet implemented:**
  - No `environment` field on `LeanLLMConfig` (the Module 2 `LeanLLMContext.environment` exists separately and is per-call/per-context, not a global config knob).
  - No `sampling_rate` field.
  - No runtime toggle for replay (replay is a class users instantiate; no global enable flag).
  - `auto_normalize` exists (config.py) but TODO checkbox under "toggle normalization" still `[ ]` because the wording asks for runtime mutation, not a load-time flag.
  - No per-request `disable_logging` — the only per-call knob is the `labels` dict.
  - No explicit "debug mode" config field (log level is controlled outside by `LEANLLM_LOG_LEVEL` in `cli.py`).
- **Test hooks / seams:**
  - `monkeypatch.setenv("LEANLLM_API_KEY", "x")` + `LeanLLMConfig.from_env()` for env path.
  - Direct constructor for unit tests: `LeanLLMConfig(database_url="sqlite:///:memory:", enable_persistence=True, batch_size=1, flush_interval_ms=20)`.

### Tests

**Target file(s):** `tests/test_config.py`

**Cases to cover:**
- [x] LeanLLMConfig() defaults match documented values (enable_persistence=True, flush_interval_ms=180_000, batch_size=100, queue_max_size=10_000, auto_migrate=True, capture_content=False)
- [x] LeanLLMConfig() default endpoint equals https://api.leanllm.dev
- [x] from_env reads LEANLLM_API_KEY into leanllm_api_key
- [x] from_env reads LEANLLM_DATABASE_URL into database_url
- [x] from_env — edge: boolean env accepts "true"/"True"/"TRUE" as True
- [x] from_env — edge: non-"true" strings ("yes", "1") resolve to False
- [x] from_env — error: both LEANLLM_DATABASE_URL and LEANLLM_API_KEY set raises ValueError
- [x] from_env reads LEANLLM_ENDPOINT override
<!-- pending: environment / sampling_rate fields, per-request disable_logging, debug-mode config -->

---

# 11. DEVELOPER EXPERIENCE (DX)

[ ] Initialization:
[x] leanllm.init(api_key)

[x] Minimal setup:
[x] auto-detect LiteLLM usage

[ ] Debug tools:
[ ] print structured logs locally
[ ] inspect last request

[ ] CLI (optional):
[ ] view recent requests
[ ] trigger replay

[x] Typing:
[x] strong typing for all objects

[x] Performance:
[x] low overhead (<5% latency impact)
[x] async-safe

### Implementation Notes

- **Files:** `leanllm/__init__.py` (public exports), `leanllm/client.py` (constructor pattern), `leanllm/cli.py` (CLI).
- **Public entry points:** `LeanLLM(api_key=..., config=...)`, `leanllm migrate {up,down,current,history}` command.
- **Key behaviors / invariants:**
  - Initialization is via constructor (`LeanLLM(api_key=...)`), **not** a module-level `leanllm.init(...)` function. The TODO item "leanllm.init(api_key)" is satisfied in spirit by the constructor but not literally by a top-level function.
  - LiteLLM is a direct dependency — auto-detection is moot; `litellm.completion` is always the execution path.
  - CLI currently covers **migrations only** (up/down/current/history). It uses `LEANLLM_DATABASE_URL` by default, overridable with `--url`.
  - All public models are Pydantic / fully typed; `__init__.py` exports only `LeanLLM`, `LeanLLMConfig`, `LLMEvent`.
  - Async safety is delivered by: (a) request path is pure sync, (b) worker runs in a daemon thread with its own asyncio loop, (c) enqueue is non-blocking `put_nowait`.
- **Edge cases observed in code:**
  - If persistence is disabled (no URL/key), `LeanLLM.chat` still returns the full LiteLLM response — the library is a no-op tracker but never breaks the call.
- **Not yet implemented:**
  - No `leanllm.init(...)` top-level helper (only the class constructor).
  - No "inspect last request" debug accessor.
  - No CLI commands to list/view events or trigger a replay.
  - No documented local-debug logging format beyond raw Python `logging`.
- **Test hooks / seams:**
  - `LeanLLM(api_key="x", config=LeanLLMConfig(enable_persistence=False))` gives a minimal client that won't start a worker — useful for tests that only exercise `_build_event`.
  - CLI tested via `cli.main(argv=[...])` with a monkeypatched `upgrade_postgres` / `current_postgres` / etc.

### Tests

**Target file(s):** `tests/test_client.py`

**Cases to cover:**
- [x] LeanLLM(api_key, config=enable_persistence=False) builds a no-op client (no worker started, _queue is None)
- [x] LeanLLM without URL/key logs info and leaves _queue/_worker as None
- [x] Public import surface exposes LeanLLM, LeanLLMConfig, LLMEvent from the leanllm package
- [x] chat returns the raw LiteLLM response object untouched (library never intercepts the return value)
<!-- pending: leanllm.init(api_key) top-level helper, inspect-last-request debug accessor, CLI view/replay commands — see Modules 13/16 -->

---

# FINAL REQUIREMENTS

[ ] All events must be:
[x] deterministic
[ ] replayable
[x] structured (not raw text blobs)

[ ] Library must:
[x] not break existing LiteLLM flows
[x] be installable via pip
[x] require minimal setup (<5 min)

[ ] Core success criteria:
[ ] developer can replay any request
[ ] developer can trace any chain
[ ] data is usable for eval/prompt/cost modules

### Implementation Notes

- **Status summary as of this mapping:**
  - *Deterministic*: ✅ `event_id` is a UUID, `timestamp` is UTC, all fields are strongly typed.
  - *Replayable*: ⚠️ engine + snapshot fields exist (parameters, tools, prompt all captured by module 1; `ReplayEngine` lands in module 5). Still gated by **needing the `LLMEvent` in memory** — there is no `store.get_event(request_id)` / `store.list_events(...)` query API. See module 12 (Storage Query API) for the gap.
  - *Structured*: ✅ `LLMEvent` is Pydantic; JSON-serializable with ISO timestamps.
  - *Doesn't break LiteLLM*: ✅ `chat()` returns the raw `litellm.ModelResponse`.
  - *Pip-installable*: ✅ `pip install leanllm-ai` with `[postgres]` / `[sqlite]` / `[remote]` extras (per `pyproject.toml`).
  - *Minimal setup*: ✅ `LeanLLM(api_key="sk-...")` works out of the box (no persistence unless configured).
  - *Replay any request*: ⚠️ requires the user to have the `LLMEvent` in hand (e.g. via `post_call_hook`). Closing this gap is module 12's job (`get_event` / `list_events`).
  - *Trace any chain*: ✅ context propagation (module 2) + lineage graph (module 6) both shipped. `Chain` advances `parent_request_id` automatically when wired as a `post_call_hook`.
  - *Data usable for eval/prompt/cost*: ✅ cost pipeline complete (module 7); normalization complete (module 4); context complete (module 2). Downstream consumers (eval, prompt) still need the storage query API to retrieve events for offline analysis.
- **Priorities implied by dependencies:** modules 1 (enrich capture) → 2 (context) → 3 (error field) → 4 (normalization) → 5 (replay) → 6 (lineage). Modules 7 (cost) and 8 (pipeline) are already load-bearing.

---

# ARCHITECTURAL DECISIONS — modules 12+

These decisions were made during scoping of the next-step modules. Treat as binding constraints when implementing.

**1. Request path is never-blocking.**
`LeanLLM.chat()` / `completion()` must not block on capture, persistence, sampling I/O, or anything else. Every side-effect runs through the existing daemon-thread worker + EventQueue (`put_nowait`). Public APIs that need I/O (e.g. `get_event`, `list_events`) are `async def` — there is **no sync wrapper** that internally calls `asyncio.run()`.

**2. No local-disk fallback in ephemeral environments.**
We follow the standard observability pattern (Sentry / Datadog / OTel exporters): in-memory queue → exponential backoff retry → drop with WARNING + counter after exhaustion. We do **not** spill to JSONL or auto-fallback to a local SQLite when the configured backend is unreachable. Reason: in Kubernetes-style ephemeral containers, persisting locally just delays the data loss while creating disk-pressure issues. Accept the drop; expose a counter; rely on operators to alert on it.

**3. Migration freedom (no events shipped yet).**
No `LLMEvent` has been written by a real user yet. Schema is free to change without backwards compat. **Bump `schema_version` to 2 if any field shape changes**, and replace migration `0002_add_module_fields` with a single fresh DDL where useful — don't accumulate incremental migrations for un-shipped state.

**4. SDK runs standalone; SaaS-dependent features deferred.**
Anything that needs a new SaaS endpoint (`GET /v1/events`, `leanllm sync`, `account_id` mapping) is stubbed (`NotImplementedError`) and explicitly noted as deferred. The SDK does not block on SaaS backend coordination. When the SaaS catches up, the stubs become wiring — not new architecture.

**5. Postgres is the production backend; SQLite is dev-only.**
SQLite remains supported for `pip install leanllm-ai[sqlite]` + local dev / tests / CI. Production self-hosted users use Postgres. SQLite is never auto-selected as a fallback at runtime.

---

# 12. STORAGE QUERY API (OSS GAP — fetching events back)

> **Why this exists:** today, replay only works for events still in memory (e.g. captured via `post_call_hook`). There is no way to fetch an event by id or list events by filter from any store. This breaks the natural workflow ("capture → look it up later → replay"). Without this, replay is technically present but practically unusable.

> **Architectural notes from decisions block:** all read APIs are `async def`. There is no sync wrapper. `RemoteEventStore` reads are deferred until the SaaS exposes `GET /v1/events`. Schema reformat is free: bump `schema_version` to 2 and consolidate migrations rather than stack 0002/0003.

[x] Async read API on `BaseEventStore`:
[x] `async def get_event(*, event_id) -> Optional[LLMEvent]`
[x] `async def list_events(*, correlation_id=None, model=None, since=None, until=None, errors_only=False, limit=100, offset=0) -> List[LLMEvent]`
[x] `async def count_events(*, ...)` (basic — counts under the same filters; supports paging UX)

[x] Backend implementations:
[x] `SQLiteEventStore` — full implementation (round-trip JSON columns back to Pydantic).
[x] `PostgresEventStore` — full implementation.
[x] `RemoteEventStore` — **stubbed** with `raise NotImplementedError("remote query API requires SaaS endpoint, see roadmap")`. Don't gate Module 12 on SaaS work.

[x] Hydration helper:
[x] `storage/_hydrate.py::row_to_event(row)` inverse of `_to_row` for SQL backends — handles nested fields (`normalized_input`, `parameters`, `tools`, `tool_calls`) and timestamp coercion across SQLite (TEXT/ISO) and Postgres (TIMESTAMPTZ).
[x] When hydration raises (corrupted row, JSON parse error), log ERROR with `event_id` and skip that row — never abort the whole `list_events` call.

[x] Public surface:
[x] `async def LeanLLM.get_event(*, event_id)` / `async def LeanLLM.list_events(...)` / `async def LeanLLM.count_events(...)` — submitted via `asyncio.run_coroutine_threadsafe` to the worker loop (the asyncpg / aiosqlite pool is bound to that loop). Caller awaits without blocking its own loop. Raises `RuntimeError` if persistence is disabled.

[x] `ReplayEngine.replay_by_id`:
[x] `async def replay_by_id(*, event_id, overrides=None)` — internally `await client.get_event(...)` then existing `replay(event=...)`. Raises `ValueError` if the event isn't found.

[x] Filter scope (cap for OSS):
[x] First pass supports filters that BOTH backends do natively without extensions: `correlation_id`, `model`, `since`/`until` (via `timestamp` index), `errors_only`, `limit`/`offset`. **No JSON-path label filtering** in v1 — would require Postgres JSONB ops + SQLite JSON1 extension (not in all distros). Documented as a follow-up.

[x] Schema bump:
[x] `LLMEvent.schema_version = 2`. Migration `0002_add_module_fields` removed; consolidated into a single `20260427_0001_initial_v2.py` reflecting the final schema. Justified by the "no events shipped yet" architectural decision.

### Implementation Notes

- **Files:** `leanllm/storage/_hydrate.py` (new), `leanllm/storage/base.py` (3 new abstracts), `leanllm/storage/sqlite.py` (read API + `_build_where`), `leanllm/storage/postgres.py` (read API + `_build_where` for `$N` placeholders), `leanllm/storage/remote.py` (read stubs), `leanllm/client.py` (public `get_event`/`list_events`/`count_events` + `_run_on_worker` helper), `leanllm/replay.py` (`replay_by_id`), `leanllm/events/worker.py` (`_loop_ready` Event so `start()` blocks until loop is live), `leanllm/storage/migrations/postgres/versions/20260427_0001_initial_v2.py` (consolidated migration).
- **Public entry points:** `client.get_event(*, event_id)` / `client.list_events(...)` / `client.count_events(...)`, `ReplayEngine.replay_by_id(*, event_id, overrides=None)`. All `async def`.
- **Key behaviors / invariants:**
  - The Postgres / SQLite connection pool is bound to the worker thread's asyncio loop. `_run_on_worker` cross-posts coroutines via `asyncio.run_coroutine_threadsafe(...)` and the caller awaits the resulting `concurrent.futures.Future` via `asyncio.wrap_future(...)` — request thread never blocks on DB I/O.
  - `EventWorker.start()` now blocks (timeout 5s) until the daemon thread has set up its loop, so `run_coroutine_threadsafe` callers never see `loop is None`.
  - `_FIELD_NAMES` in `_hydrate.py` is the single source of truth for the column order — both `SELECT` lists and the SQLite tuple-to-dict mapping use it. Any future schema change must edit this tuple in lockstep with `_to_row`.
  - Hydration is per-row defensive: a corrupt row (bad JSON, type mismatch) is logged at ERROR level with `event_id` and skipped; `list_events` returns the rest.
  - Filters are AND-combined; ordering is `timestamp DESC` for `list_events` (not for `count_events`, which doesn't need it).
- **Edge cases observed in code:**
  - `SQLiteEventStore.get_event` / `list_events` / `count_events` return `None` / `[]` / `0` when called before `initialize()` — same defensive behavior as `save_batch`.
  - Postgres `_build_where` numbers placeholders by `len(params)+1` after each clause; the final `LIMIT $N OFFSET $N+1` indices are computed off the already-built params list.
  - `LeanLLM.get_event` raises `RuntimeError` (not `NotImplementedError`) when persistence is disabled — this is a config error, not a missing feature.
  - `RemoteEventStore.get_event` raises `NotImplementedError` with a clear message pointing at the SaaS roadmap; users aren't surprised at runtime when a `RemoteEventStore` is in use.
  - `ReplayEngine.replay_by_id` is the only async method on the engine — the rest stay sync.
- **Thread/async boundaries:**
  - All read APIs run on the worker's asyncio loop. The caller can be on any loop or thread (must be inside an asyncio context — there is no sync wrapper, by architectural decision).
  - `EventQueue.enqueue` (request thread) and the worker drain are unaffected.
- **Test hooks / seams:**
  - Direct: `await store.get_event(event_id="x")` against a `:memory:` SQLite is the fastest path.
  - End-to-end: enqueue an event into `client._queue`, wait on `client._store.count_events()` via cross-thread submission, then `await client.get_event(event_id="x")`.
  - Corruption: directly UPDATE a row's JSON column with garbage and assert `list_events` skips + logs.

---

# 13. CLI — LOGS & REPLAY (OSS GAP — debug ergonomics)

> **Why this exists:** CLI today only does Alembic migrations. A developer using LeanLLM in dev cannot list recent calls or rerun one without writing custom Python. This is a hard requirement for OSS adoption.

> **Architectural notes from decisions block:** CLI runs against the local store backend (Postgres / SQLite via `LEANLLM_DATABASE_URL`). When only `LEANLLM_API_KEY` is set, query commands abort with a clear "remote query requires SaaS API (deferred)" message — they don't try to call the SaaS. CLI uses the same async APIs from Module 12 (driven from within an `asyncio.run()` invocation in the CLI entrypoint, since the CLI is a one-shot process — async-in-CLI is fine, just not in the SDK request path).

[x] `leanllm logs` — list recent events:
[x] flags: `--limit`, `--offset`, `--correlation-id`, `--model`, `--since`, `--until`, `--errors-only`, `--format=table|json`
[x] default format: ASCII table (no Rich dep) — columns `event_id | timestamp | model | latency_ms | tokens | cost | error_kind`. `--format=json` outputs JSONL for piping to `jq`.
[x] uses `LeanLLMConfig.from_env()` to pick the store (same precedence as the client). Aborts with clear message + exit code 2 if only `LEANLLM_API_KEY` is set.
[x] `parse_when` accepts ISO-8601 ("2026-04-27", "2026-04-27T10:00:00", with or without tz) and relative ("1h", "30m", "2d") — naive ISO is treated as UTC.

[x] `leanllm show <event_id>` — single-event detail:
[x] full `LLMEvent.model_dump_json(indent=2)` by default
[x] `--pretty` calls `event.pretty_print()` (Module 16)
[x] not-found returns exit code 1 with stderr message

[x] `leanllm replay <event_id>`:
[x] prints `ReplayResult.summary()` by default; `--print-diff` switches to `pretty_print()` with unified diff
[x] flags: `--model <override>`, `--temperature <override>`, `--print-diff`
[x] uses a non-persisting `LeanLLM` client for the actual provider call — avoids creating a duplicate event in the store

[x] `leanllm replay --batch <file>` — batch replay:
[x] file: one event_id per line; `#` comments and blank lines ignored
[x] prints aggregate stats (`replays`, `errors`, `text_diffs`, `total_token_delta`, `total_latency_delta`)
[x] missing ids are warned + skipped; the batch never aborts on partial failure
[x] exit code 0 if all succeeded, 1 if any failed

[x] CLI plumbing:
[x] `leanllm/cli.py` replaced by `leanllm/cli/` package: `__init__.py` (dispatch) + `migrate.py` + `logs.py` + `show.py` + `replay.py` + `_store.py` (shared async store opener with `parse_when`)
[x] subcommand registration via argparse subparsers (no new deps — argparse only)
[x] each async subcommand sets `_is_async=True` on the namespace; dispatcher wraps with `asyncio.run(...)`. Migrate stays sync (Alembic).

### Implementation Notes

- **Files:** `leanllm/cli/__init__.py` (dispatcher + entry point — `leanllm.cli:main` still resolves), `leanllm/cli/migrate.py` (extracted from old `cli.py` — sync), `leanllm/cli/logs.py` (`leanllm logs`), `leanllm/cli/show.py` (`leanllm show`), `leanllm/cli/replay.py` (`leanllm replay` single + `--batch`), `leanllm/cli/_store.py` (shared `open_store` async helper + `parse_when` time arg parser). Old monolithic `cli.py` deleted.
- **Public entry points:** `leanllm migrate {up,down,current,history}`, `leanllm logs [...]`, `leanllm show <event_id> [--pretty]`, `leanllm replay <event_id> [...]`, `leanllm replay --batch <file> [...]`. The `leanllm = "leanllm.cli:main"` script in `pyproject.toml` keeps working.
- **Key behaviors / invariants:**
  - **Async-in-CLI is fine** because the CLI is a one-shot process. The dispatcher inspects `args._is_async` and wraps the coroutine in `asyncio.run(...)`. The SDK request path is unaffected.
  - **CLI opens its own store** (`open_store`) directly on the dispatcher's loop — does NOT reuse the SDK worker (which lives in a daemon thread bound to its own loop). `auto_migrate=False` so the CLI never silently migrates a Postgres schema during a `logs` query.
  - **CLI refuses Remote**: `_store.open_store` raises with a clear "remote query requires SaaS API (deferred)" message + exit 2 when only `LEANLLM_API_KEY` is set. Module 12's stub would already raise `NotImplementedError`, but the explicit guard gives a better error before initialization runs.
  - **Replay uses a non-persisting client** (`LeanLLMConfig(enable_persistence=False)`) so the replayed call doesn't create a duplicate event in the same store. Provider credentials (`OPENAI_API_KEY`, etc.) come from the env via LiteLLM.
  - **Batch replay** aggregates `successes`/`failures`/`text_diffs`/`total_token_delta`/`total_latency_delta` and prints a single summary line at the end. Per-item summaries print first (or `pretty_print` when `--print-diff`).
  - **Time parsing** (`parse_when`) is a tight helper: ISO-8601 (date or datetime, naive treated as UTC) or relative `Nh`/`Nm`/`Nd`. Anything else raises `ValueError` with no clever guesses.
- **Edge cases observed in code:**
  - Empty store + `logs` → table renders header + separator only; JSON format prints nothing.
  - Batch file: `#` comments and blank lines are skipped silently; ids with whitespace are stripped.
  - `replay <id>` without `<id>` and without `--batch` returns exit 2 with a stderr message — argparse can't enforce this because `event_id` is `nargs="?"` (so `--batch` works alone).
  - `show` not-found → exit 1, stderr message. Distinguished from "store unreachable" (exit 2 from `open_store`).
  - Migrate stays sync — the dispatcher branches on `_is_async`, so mixing sync (alembic) and async (queries) commands in the same parser works naturally.
- **Test hooks / seams:**
  - `tests/test_cli.py` is fully sync. SQLite file (not `:memory:`) is seeded via a one-shot `asyncio.run(...)` in a helper, then the CLI's own `asyncio.run` runs against it.
  - `monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)` for replay tests — keeps the test off the network.
  - `_clean_env` autouse fixture strips `LEANLLM_*` env vars so tests are deterministic.
  - `parse_when` is pure — assert against datetimes directly with a small `< 5 seconds` tolerance for the relative branches.

### Tests

**Target file(s):** `tests/test_cli.py`

**Cases to cover:**
- [x] `parse_when` ISO date / ISO datetime with tz / naive ISO treated as UTC
- [x] `parse_when` relative `1h` / `30m` / `2d`
- [x] `parse_when` empty raises ValueError
- [x] `logs` default table renders header + event row
- [x] `logs --format json` outputs one JSON object per line
- [x] `logs --correlation-id` filters
- [x] `logs --model` filters
- [x] `logs --errors-only` filters
- [x] `logs --limit / --offset` paginates correctly (newest-first)
- [x] `logs` against empty store renders only the header
- [x] `logs` aborts (exit 2) when only `LEANLLM_API_KEY` is set
- [x] `show <id>` prints model_dump_json by default
- [x] `show <id> --pretty` uses sectioned view
- [x] `show` not-found returns exit 1 with stderr
- [x] `replay <id>` prints ReplayResult.summary
- [x] `replay <id> --print-diff` prints pretty_print with diff
- [x] `replay` not-found returns exit 1
- [x] `replay --model X --temperature 0` overrides flow into the client call
- [x] `replay --batch <file>` runs aggregate
- [x] `replay --batch` warns about missing ids and continues
- [x] `replay` without event_id and without `--batch` returns exit 2

---

# 14. PER-REQUEST + RUNTIME CONFIG TOGGLES (OSS GAP — control surface)

> **Why this exists:** OSS users in production want to disable logging for hot paths, sample noisy endpoints, and switch redaction per call. Today the only per-call knob is `labels`.

> **Architectural notes from decisions block:** sampling is **producer-side** — the decision happens before `_build_event_from_response` runs, so sampled-out calls cost ~one `random.random()` and nothing else (no LLMEvent constructed, no enqueue). Errors **bypass sampling** (always logged) — yes, this means the observed retention rate isn't pure `sampling_rate`; that's the right tradeoff for an operational signal. `account_id` mapping is **deferred** (SaaS-shaped — adds a field to the outgoing payload that only the SaaS consumes).

[x] Per-request kwargs on `LeanLLM.chat` / `LeanLLM.completion`:
[x] `log: bool = True` — when `False`, the call still hits LiteLLM but no `LLMEvent` is built/enqueued (and no hooks fire). Keyword-only.
[x] `redaction_mode: Optional[RedactionMode] = None` — overrides `config.redaction_mode` for this call only.
[x] `sample: Optional[float] = None` — coerces sampling decision for this call (`0.0` always drops, `1.0` always keeps).

[x] Global sampling (producer-side):
[x] `LeanLLMConfig.sampling_rate: float = 1.0`
[x] `LEANLLM_SAMPLING_RATE` env var (parsed as float)
[x] decision happens **before** `_build_event_*` runs: `if random.random() >= rate and not is_error_path: skip everything`. Errors are always logged regardless of sampling.
[x] document the consequence in code: observed retention rate ≠ `sampling_rate` when error rate is nonzero; this is intentional.

[x] Environment field:
[x] `LeanLLMConfig.environment: Optional[str] = None` (`LEANLLM_ENVIRONMENT` env var)
[x] mirrored into `LLMEvent.metadata["environment"]` when set, so the same store can hold dev/staging/prod traffic.
[x] precedence: per-call `LeanLLMContext.environment` wins over `LeanLLMConfig.environment`. Resolved once via `_resolve_environment(config, context)` helper.

[x] Debug mode:
[x] `LeanLLMConfig.debug: bool = False` (`LEANLLM_DEBUG`)
[x] when `True`, sets the `leanllm` logger to `DEBUG` and emits a one-line summary of every captured event to stderr (uses `event.summary()` — implemented as part of this module since it was a one-liner; Module 16 expands the surface with `pretty_print()`).

[ ] **Deferred** (out of scope for v1, SaaS-shaped):
[ ] `account_id` field on outgoing payload — the SaaS already derives it from the bearer token; explicit mapping is a SaaS decision, not an SDK one.

### Implementation Notes

- **Files:** `leanllm/config.py` (3 new fields + `from_env` wiring), `leanllm/events/models.py` (`LLMEvent.summary()`), `leanllm/client.py` (3 new kwargs on `chat` / `completion`, `_should_sample` + `_resolve_environment` helpers, debug-mode logger setup, stderr summary on `_enqueue`).
- **Public entry points:**
  - `client.chat(..., *, log=True, sample=None, redaction_mode=None, **kwargs)` and the same on `completion`.
  - `LLMEvent.summary()` — one-liner human-readable string. Used by debug mode now and the CLI/Module 16 later.
  - Config: `LeanLLMConfig(sampling_rate=..., environment=..., debug=...)` and the matching `LEANLLM_*` env vars.
- **Key behaviors / invariants:**
  - **`log=False`** is a hard bypass: no pre-call hook, no event built, no enqueue, no post-call hook. The LiteLLM call still runs and the response is returned untouched. Use case: health checks, hot-path probes.
  - **Sampling is producer-side**: the rate is checked once per `chat()` (right after `log=True` is established), and the decision is stamped into `pre_call["sampled_in"]`. Sampled-out success calls skip `_emit` entirely → no event built, no DB write. Pre-call hook still fires (sampling controls *persistence*, not *observability*).
  - **Errors bypass sampling unconditionally** — `_emit_error` runs even when `sampled_in=False`. Documented consequence: observed retention rate ≠ `sampling_rate` when error rate is nonzero. This is intentional (operational signal must not be sampled away).
  - **Per-call overrides win over global config**: `sample > config.sampling_rate`, `redaction_mode > config.redaction_mode`. Both are passed as keyword-only.
  - **Environment resolution:** `LeanLLMContext.environment > LeanLLMConfig.environment`. Resolved once in `chat()` via `_resolve_environment` and stamped into `pre_call["environment"]`, so all three event builders (response / stream / error) read from the same place. Lands in `LLMEvent.metadata["environment"]` only when non-None — keeps payloads small.
  - **Debug mode:** sets the `leanllm` logger level to DEBUG at construction, and prints `event.summary()` to stderr inside `_enqueue` (so even sampled-out → no-summary, but error events still print). Stderr was chosen over the standard logger to keep the line readable in interactive sessions without log formatter noise.
- **Edge cases observed in code:**
  - `log=False` on a streaming call also bypasses — returns the raw iterator from LiteLLM; the wrapper isn't installed, so no event is ever produced.
  - `sample=0.0` per-call still runs the pre-call hook. `log=False` does not.
  - `_resolve_environment` returns `None` when neither config nor context has it set; the metadata key is then absent (not present-but-None).
  - Per-call `redaction_mode` propagates to error events too — useful when a hot path uses METADATA_ONLY for success but you want REDACTED on errors for context.
  - `_should_sample` clamps: `rate >= 1.0` → always True, `rate <= 0.0` → always False. No `random.random()` call in those edges.
- **Thread/async boundaries:**
  - All decisions (sampling, log toggle, redaction override) run on the request thread before LiteLLM is invoked. No I/O, no blocking. The `random.random()` is nanoseconds.
  - Debug stderr write is synchronous on the request thread by design — debug mode is for dev-time visibility, not production hot paths.
- **Test hooks / seams:**
  - `_should_sample(rate=...)` is pure — `monkeypatch.setattr(random, "random", ...)` to test the boundary.
  - `_resolve_environment(config=..., context=...)` is pure — no monkeypatching needed.
  - For sampling tests, set `config.sampling_rate=0.0` and assert `post_call_hook` never fires; for the error-bypass case, raise from `chat_completion` and assert `error_hook` still fires.
  - For debug mode, `capsys.readouterr().err` captures the summary line.

### Tests

**Target file(s):** `tests/test_runtime_toggles.py`

**Cases to cover:**
- [x] `_should_sample` rate=1.0 always keeps; rate>=1 keeps; rate<=0 drops
- [x] `_should_sample` partial uses `random.random()` (monkeypatch)
- [x] `_resolve_environment`: context wins over config
- [x] `_resolve_environment`: falls back to config when context.environment is None
- [x] `_resolve_environment`: returns None when neither set
- [x] `LeanLLMConfig` defaults: sampling_rate=1.0, environment=None, debug=False
- [x] `from_env` reads `LEANLLM_SAMPLING_RATE` (float)
- [x] `from_env` reads `LEANLLM_ENVIRONMENT`
- [x] `from_env` reads `LEANLLM_DEBUG`
- [x] `log=False` bypasses pre_call_hook + post_call_hook + event emission
- [x] `log=True` (default) still emits
- [x] global `sampling_rate=0.0` drops success events
- [x] global `sampling_rate=0.0` still emits errors (error_hook fires)
- [x] per-call `sample=0.0` drops
- [x] per-call `sample=1.0` keeps even when global is 0.0
- [x] pre_call_hook fires even when sampled out (sampling ≠ observability)
- [x] per-call `redaction_mode=FULL` overrides default (METADATA_ONLY → captured prompt/response)
- [x] per-call `redaction_mode=REDACTED` masks PII
- [x] config.environment lands in `LLMEvent.metadata["environment"]`
- [x] context.environment overrides config.environment
- [x] environment absent from metadata when neither is set
- [x] environment lands on error events too
- [x] debug=True sets `leanllm` logger to DEBUG level
- [x] debug=True prints `event.summary()` to stderr
- [x] debug=False does not print
- [x] `LLMEvent.summary()` success format includes model + tokens + cost + latency
- [x] `LLMEvent.summary()` error format includes ERROR(kind) + message

---

# 15. RESILIENT IN-MEMORY DELIVERY (was: OFFLINE FALLBACK — REDESIGNED)

> **Why this exists (revised):** the original design (JSONL spill + auto-fallback to local SQLite when the remote is unreachable) was rejected as inappropriate for ephemeral container deployments — the canonical observability pattern (Sentry / Datadog / OTel exporters) is in-memory queue + aggressive retry + drop with WARNING after exhaustion. Persisting to local disk in a Kubernetes pod just delays data loss while creating disk-pressure issues. We follow the standard pattern: **drop is acceptable; silent drop is not.**

> **Architectural notes from decisions block:** no JSONL spillover. No `~/.leanllm/events.db` magic. No `leanllm sync`. SQLite stays as a dev-only / explicit self-hosted backend, never auto-selected. The "don't lose data" promise is delivered by **bigger queue + smarter retry + visible counter**, not by writing to disk.

[x] Tighten the existing retry loop in `EventWorker._flush_with_retry`:
[x] `LeanLLMConfig.retry_max_attempts: int = 5` (was hardcoded 3) + `LEANLLM_RETRY_MAX_ATTEMPTS`.
[x] `LeanLLMConfig.retry_initial_backoff_ms: int = 500` (was hardcoded 0.5s) + `LEANLLM_RETRY_INITIAL_BACKOFF_MS`. Doubles on each attempt (`base * 2 ** attempt`).
[x] `LeanLLMConfig.retry_total_budget_ms: int = 30_000` + `LEANLLM_RETRY_TOTAL_BUDGET_MS`. Drop early when the next sleep would exceed the budget — prevents a stuck remote from holding the worker hostage.
[x] Jitter (`wait * (1 + random.uniform(-0.2, +0.2))`) applied to every backoff sleep — spreads retries across replicas during recovery.

[x] Visible drop counter on the client:
[x] `LeanLLM.dropped_events_count` — sum of queue-full drops + worker-side post-retry drops.
[x] `LeanLLM.events_in_flight` — queue size + currently-flushing batch size. Read-safe from any thread (atomic reads of int counters).
[x] Worker exposes `dropped_events_count`, `dropped_batches_count`, `inflight_count` for fine-grained inspection.

[x] Rate-limited drop warnings:
[x] queue-full drop log: kept the existing "every 100th drop" scheme (Module 8).
[x] worker-side batch drop: ERROR log gated by a 60s window (`_DROP_LOG_WINDOW_SECONDS`). Reports cumulative `dropped_since_last_log` since the last emit. First drop logs immediately (window starts at 0).

[x] Optional pre-flush hook for ops integrations:
[x] `LeanLLM(api_key=..., on_dropped_events=callback)` — keyword-only constructor kwarg (NOT in `LeanLLMConfig` because Pydantic doesn't serialize callables; matches the existing pre/post/error hook pattern).
[x] Signature: `Callable[[int, str], None]` — args are `(batch_size, reason)`. Reason includes either `"max retries reached: <exc>"` or `"retry budget exceeded after N attempts: <exc>"`.
[x] Runs on the worker thread. Exceptions in the callback are logged + swallowed so a buggy hook can't break event capture.

[x] **Explicitly NOT in scope (rejected):**
[ ] no JSONL spillover file
[ ] no automatic fallback to local SQLite when the configured backend fails
[ ] no `leanllm sync` (was SaaS-shaped)
[ ] no `~/.leanllm/events.db` mythical fallback path

### Implementation Notes

- **Files:** `leanllm/config.py` (3 new int fields + `from_env`), `leanllm/events/worker.py` (rewritten retry loop with budget cap + jitter + drop counter + rate-limited log + callback), `leanllm/client.py` (`on_dropped_events` constructor kwarg, `dropped_events_count` + `events_in_flight` properties, propagation into worker).
- **Public entry points:**
  - Config: `LeanLLMConfig.retry_max_attempts`, `retry_initial_backoff_ms`, `retry_total_budget_ms` + matching env vars.
  - Constructor: `LeanLLM(api_key=..., config=..., on_dropped_events=callback)`.
  - Properties: `client.dropped_events_count`, `client.events_in_flight`.
- **Key behaviors / invariants:**
  - **Retry attempts** is `max_retries` total tries (1 = drop after first failure, no retry). Exponential backoff `base * 2 ** attempt` with `±20%` jitter.
  - **Budget cap is projected**: before each sleep the worker checks `elapsed + planned_wait > budget`. If so, drop *before* sleeping. This means batches don't sit waiting through a sleep that they'll abort anyway.
  - **Jitter is applied to every sleep** (between attempts), not the final attempt. The last attempt has no sleep after — it just drops.
  - **Drop record** updates `_dropped_events`, `_dropped_batches`, `_dropped_since_last_log`, then optionally logs (rate-limited) and fires the callback.
  - **Rate-limited log**: window is `_DROP_LOG_WINDOW_SECONDS = 60.0`. First drop logs (`_last_drop_log_ts == 0` initially). Subsequent drops within the window only update the counter; the next log line summarizes the accumulated drops.
  - **Callback runs synchronously on the worker thread**. User code is responsible for not blocking it; the worker swallows callback exceptions to keep the daemon alive.
  - **Visibility**: `client.dropped_events_count` aggregates `EventQueue.dropped` (queue-full path, request thread) + `EventWorker.dropped_events_count` (post-retry, worker thread). Both are simple int reads, no locking needed.
- **Edge cases observed in code:**
  - `max_retries=1` → no retry; one attempt, then drop. Useful for hot paths where you'd rather lose data than buffer.
  - `total_budget_ms=0` → drops on first failure (budget is exhausted by any positive sleep).
  - `initial_backoff_ms=0` → still loops up to `max_retries` but with no sleep between attempts (retries as fast as the store can fail).
  - Worker swap during a test (`worker._store = fake`) is supported for direct `_flush_with_retry` invocations — useful for callback / counter assertions.
- **Thread/async boundaries:**
  - All retry / drop logic runs on the worker's asyncio loop on its daemon thread.
  - Counters (`_dropped_events`, `_inflight_count`, `_dropped_batches`) are int and updated only on the worker thread; reads from other threads are best-effort but safe (CPython int atomicity under the GIL).
  - Callback is invoked from the worker thread — user code that touches threadlocal state must understand this.
- **Test hooks / seams:**
  - `EventWorker(max_retries=1, initial_backoff_ms=10)` for fast deterministic drop tests.
  - `EventWorker(initial_backoff_ms=100, total_budget_ms=50)` to exercise the budget-cap path explicitly.
  - `worker._store = some_fake` + `_asyncio.run_coroutine_threadsafe(worker._flush_with_retry([ev]), worker._loop).result()` to drive a single batch synchronously — bypasses the queue/tick loop.
  - `monkeypatch.setattr(random, "uniform", spy)` to assert jitter range.
  - `caplog.at_level(logging.ERROR)` to count drop log lines (one per 60s window).

### Tests

**Target file(s):** `tests/test_resilient_delivery.py` + updates to `tests/test_worker.py`

**Cases to cover:**
- [x] config defaults: max_attempts=5, initial_backoff_ms=500, total_budget_ms=30_000
- [x] `from_env` reads the 3 new env vars
- [x] retry succeeds before exhaustion (fail_first_n=2, max_retries=5 → 3 calls, no drops)
- [x] `max_retries=1` drops on first failure
- [x] total budget cap drops before max_retries when next sleep would exceed
- [x] jitter: `random.uniform(-0.2, 0.2)` invoked once per retry sleep
- [x] `on_dropped` callback fires once per dropped batch with `(size, reason)`
- [x] callback exception swallowed; worker keeps recording drops
- [x] first drop emits ERROR log immediately
- [x] subsequent drops within 60s window do NOT log; counter still increments
- [x] `client.dropped_events_count` aggregates queue + worker drops
- [x] `client.events_in_flight` returns 0 with persistence disabled
- [x] `on_dropped_events` constructor kwarg propagates to worker
- [x] (kept) test_worker existing 3 tests pass with tightened retry config

---

# 16. DEVELOPER ERGONOMICS — pretty print, summary, last-request, init helper

> **Why this exists:** the data is rich but reading it is friction. A few small affordances make the difference between "raw JSON dump" and "I see what happened".

[x] `LLMEvent.summary() -> str`:
[x] one-line human-readable, e.g. `"[2026-04-27 14:23:01] gpt-4o tokens=345/512 cost=$0.0023 latency=1400ms"`
[x] error variant: `"[…] gpt-4o ERROR(rate_limit): too many requests"`

[x] `LLMEvent.pretty_print(file=sys.stdout) -> None`:
[x] multi-line, sectioned: meta (ids, model, status, environment), tokens/cost/latency block, input (truncated to 800 chars by default), output (truncated), tool_calls if any, error_kind/message if any.
[x] `truncate=` kwarg controls input/output cap; `None` disables.

[x] `ReplayResult.summary()` / `pretty_print()`:
[x] surfaces `text_identical`, `tokens_delta`, `latency_ms_delta`, condensed unified diff. Error branch short-circuits to a single ERROR line.

[x] `LeanLLM.last_event` accessor:
[x] in-memory ring buffer of size `LeanLLMConfig.last_event_buffer = 32` (configurable, `0` disables).
[x] `client.last_event` returns the most recent event; `client.recent_events(n=8)` returns the tail.
[x] **only kept in-process, in-memory** — does not replace storage; intended for `python -i` / Jupyter / dev-time inspection. Aligned with the "no local disk fallback" decision: this is process memory, not persistence.
[x] populated from `_enqueue` (request thread, after the event is built) — consistent with what the user just saw on the request side, independent of whether the worker has flushed yet.

[x] `leanllm.init(*, api_key="", config=None, **kwargs)` top-level helper:
[x] returns a process-wide singleton `LeanLLM` and stashes it under `leanllm._default_client`. Idempotent — a second `init()` returns the same instance.
[x] convenience wrappers `leanllm.chat(...)` / `leanllm.completion(...)` route to the singleton (with a `RuntimeError` if not initialized).
[x] `leanllm.shutdown()` stops the singleton's worker and drops the reference, so a fresh `init()` can rebuild.
[x] `leanllm.get_default_client()` returns the current singleton (or `None`).
[x] Purely syntactic sugar — class-based usage stays the canonical path. Documented as "convenience for scripts / notebooks; libraries should use the class explicitly".

[x] Auto-chain mode (opt-in):
[x] `LeanLLMConfig.auto_chain: bool = False` (`LEANLLM_AUTO_CHAIN`).
[x] when `True`, a **separate ContextVar** `_auto_chain_var: ContextVar[Optional[str]]` (NOT folded into `LeanLLMContext`) holds the last `event_id` of the current async task / sync stack. After every `_enqueue` (success **or** error), the var is set to `event.event_id`. On the next `chat()` in the same task, `parent_request_id` resolves to that value if no explicit value was passed.
[x] outer `trace()` resets the auto-chain var (new correlation = new chain). Existing `trace()` token reset still applies on exit.
[x] explicit `chain.kwargs()` / explicit `parent_request_id=` kwarg always wins — the auto-chain only fills the gap when nothing was provided.
[x] documented consequence: aggressive sampling can leave chains with `parent_request_id` pointing to events that weren't persisted, because sampled-out events don't reach `_enqueue` and don't advance the chain.

### Implementation Notes

- **Files:** `leanllm/events/models.py` (`LLMEvent.pretty_print` + `_truncate` helper), `leanllm/replay.py` (`ReplayResult.summary` / `pretty_print`), `leanllm/config.py` (`last_event_buffer`, `auto_chain` fields + `from_env` wiring), `leanllm/context.py` (new `_auto_chain_var` ContextVar + `get_auto_chain_parent` / `set_auto_chain_parent`; `trace()` resets the chain on entry/exit), `leanllm/client.py` (in-memory ring buffer in `__init__`, `last_event` / `recent_events` properties, auto-chain fallback in `chat()`, advance in `_enqueue`), `leanllm/__init__.py` (`init` / `shutdown` / `chat` / `completion` / `get_default_client` top-level helpers).
- **Public entry points:**
  - `LLMEvent.pretty_print(file=sys.stdout, *, truncate=800)`
  - `ReplayResult.summary()` / `ReplayResult.pretty_print(file=sys.stdout)`
  - `client.last_event` (property), `client.recent_events(n=8)` (method)
  - `leanllm.init(api_key="", config=None, **kwargs)`, `leanllm.chat(...)`, `leanllm.completion(...)`, `leanllm.get_default_client()`, `leanllm.shutdown()`
  - `LeanLLMConfig.last_event_buffer`, `LeanLLMConfig.auto_chain`, env vars `LEANLLM_LAST_EVENT_BUFFER`, `LEANLLM_AUTO_CHAIN`
- **Key behaviors / invariants:**
  - **Pretty print is line-buffered**: builds a `List[str]` then writes once with a trailing newline. Predictable for capture in tests (no incremental writes).
  - **Truncation**: appends `... <truncated N chars>` so the user knows there's more. `truncate=None` disables — useful when piping to a file or full inspection.
  - **`_recent_events`** is a `collections.deque(maxlen=...)` — O(1) append + automatic eviction. `last_event_buffer=0` sets `_recent_events_enabled=False` (deque uses maxlen=1 internally to keep the type stable but never appends).
  - **Buffer is populated in `_enqueue`** (request thread) — independent of whether `_queue` is set, so users running with `enable_persistence=False` still get inspection.
  - **Singleton (`leanllm.init`)** is idempotent: second call returns the same instance regardless of the args. To rebuild, call `shutdown()` first.
  - **Auto-chain ContextVar** is intentionally *separate* from `LeanLLMContext` (Module 2). It's worker bookkeeping; users never touch it directly. Lookup cost in `chat()` is one ContextVar read when `auto_chain=True`, zero otherwise (early skip).
  - **Auto-chain advance** lives in `_enqueue`, after the buffer append. So both sampled-out (`_emit` not called → no advance) and `log=False` (no `_enqueue`) cases are correctly excluded; success and error events both advance.
- **Edge cases observed in code:**
  - `recent_events(n=0)` returns `[]` (matches `last_event_buffer=0` semantics).
  - `recent_events(n=100)` on a buffer with 3 events returns those 3 — never raises.
  - `pretty_print` with no prompt/response/tool_calls/error renders only header + tokens/cost/latency. No empty sections.
  - `ReplayResult.pretty_print` short-circuits on `error_message` set — doesn't print tokens/latency lines that would be misleading.
  - `auto_chain=True` + explicit `parent_request_id="X"` → `X` wins (auto-chain only fills the gap).
  - `auto_chain=True` + `trace()` enter → first call inside has parent=`None` (chain reset). On `trace()` exit, the outer auto-chain is restored via the token.
  - `leanllm.shutdown()` is safe to call when no singleton exists (no-op).
  - `leanllm.chat(...)` without prior `leanllm.init(...)` raises `RuntimeError` — never silently falls back to a fresh client (would hide bugs).
- **Thread/async boundaries:**
  - Auto-chain uses `contextvars.ContextVar` → automatic propagation across `asyncio.create_task` / `asyncio.gather`, isolation per thread (same semantics as Module 2's `_current_context`).
  - The ring buffer is `collections.deque` which is thread-safe for `append` + `[-1]` reads in CPython (single GIL op).
  - `pretty_print` writes to whatever `TextIO` the user passes — synchronous by design (it's a debug/inspect helper, not part of the request path).
- **Test hooks / seams:**
  - `io.StringIO()` for `pretty_print` capture.
  - For singleton tests, the `_reset_singleton` fixture (`leanllm.shutdown()` + yield + `leanllm.shutdown()`) keeps tests isolated.
  - `set_auto_chain_parent(event_id=None)` at test start resets the ContextVar — needed because pytest may re-use a process across tests and the var would carry over.
  - For async-inheritance tests, run the `chat()` calls inside `asyncio.run(...)` and assert `parent_request_id` propagation across `await`.

### Tests

**Target file(s):** `tests/test_dx.py`

**Cases to cover:**
- [x] `pretty_print` renders header + token/cost/latency block
- [x] `pretty_print` includes correlation_id / parent / environment when set
- [x] `pretty_print` truncates long prompt/response with explicit marker
- [x] `pretty_print` `truncate=None` disables truncation
- [x] `pretty_print` omits prompt/response sections when both are None
- [x] `pretty_print` renders tool_calls block
- [x] `pretty_print` renders error section
- [x] `ReplayResult.summary` identical / different / error branches
- [x] `ReplayResult.pretty_print` includes diff
- [x] `ReplayResult.pretty_print` error branch short-circuits
- [x] `last_event` is None until first chat
- [x] `last_event` returns most recent
- [x] `recent_events(n)` returns tail in insertion order
- [x] `recent_events(n=0)` returns []
- [x] `last_event_buffer=0` disables the buffer entirely
- [x] buffer evicts oldest when full
- [x] buffer works when persistence is disabled
- [x] `leanllm.init` is idempotent (returns same singleton)
- [x] `leanllm.get_default_client` is None until init
- [x] top-level `chat` / `completion` route to singleton
- [x] top-level `chat` raises without `init`
- [x] `shutdown` drops singleton so a fresh `init` rebuilds
- [x] `auto_chain=False` leaves parent_request_id None across calls
- [x] `auto_chain=True` advances parent across consecutive calls
- [x] explicit `parent_request_id=` kwarg wins over auto-chain
- [x] `trace()` resets auto-chain var on entry; first inner call has parent=None
- [x] `auto_chain=False` ignores any externally-set var
- [x] auto-chain inherits across asyncio tasks via ContextVar
- [x] `get_auto_chain_parent` / `set_auto_chain_parent` round-trip

---

# 17. PRIORITIZATION (post-feedback, post-architectural-decisions)

Order to land the modules above. Each is independently shippable.

1. **Module 12 — Storage Query API.** ✅ done (v0.4.0).
2. **Module 14 — per-request toggles + producer-side sampling + environment.** ✅ done (v0.5.0).
3. **Module 16 — DX helpers (`summary` / `pretty_print` / `last_event` / `init` / `auto_chain`).** ✅ done (v0.6.0).
4. **Module 13 — CLI logs/replay.** ✅ done (v0.7.0).
5. **Module 15 — Resilient delivery (configurable retry + budget cap + jitter + drop counter + callback).** ✅ done (v0.8.0).

**All planned modules shipped.** Next steps belong to a v1.0 roadmap (docs, examples, ergonomics polish, eventual SaaS-side endpoints to unstub `RemoteEventStore`).

> **Out of scope (deliberately):** automatic optimization, embedding-based diff, eval/prompt frameworks, UI, account-level masking, JSONL spillover, local-disk fallback in containers, `leanllm sync`, SaaS-dependent CLI/API surfaces. The first set belongs to LeanLLM Pro / SaaS; the second set was specifically rejected (see "Architectural Decisions" block above the Module 12 header).

