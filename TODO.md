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
  - Module skeletons that exist but have unimplemented behavior:
    - `context.py` — `LeanLLMContext` Pydantic model (fields + `merged_labels()` helper). Propagation machinery (ContextVar, async inheritance) is module 2's job.
    - `redaction.py` — `RedactionMode` enum + `RedactionPolicy` Pydantic model. `apply()` only handles `FULL` and `METADATA_ONLY`; `REDACTED` raises `NotImplementedError` pending module 9.
    - `replay.py` — `ReplayRequest`, `ReplayResult`, `ReplayEngine` class with `NotImplementedError` methods pending module 5.
    - `normalizer.py` — `NormalizedInput`, `NormalizedOutput`, three enums (`InputType`, `OutputType`, `LengthBucket`). `normalize_input()`/`normalize_output()` raise `NotImplementedError` pending module 4.
- **Dependency graph (for planning later modules):** module 2 fills in `context.py` propagation; module 4 fills in `normalizer.py` logic; module 5 fills in `replay.py`; module 9 fills in `redaction.py` masking.
- **Test hooks / seams:**
  - `create_store(api_key=..., database_url=...)` callable directly with overrides.
  - `LeanLLMConfig` is a Pydantic `BaseModel` — construct with overrides or use `from_env()` after `monkeypatch.setenv(...)`.
  - `LeanLLMContext.merged_labels(extra={...})` produces the label dict to attach to `LLMEvent.labels`.
  - `RedactionPolicy(mode=RedactionMode.REDACTED)` + `apply(...)` currently raises — useful as a pending-work marker.

### Tests

**Target file(s):** `tests/test_storage_factory.py`, `tests/test_context.py`, `tests/test_replay.py`, `tests/test_normalizer.py`

**Cases to cover:**
- [ ] create_store with api_key returns RemoteEventStore (api_key wins over database_url)
- [ ] create_store with postgresql URL returns PostgresEventStore
- [ ] create_store with sqlite URL returns SQLiteEventStore
- [ ] create_store — edge: sqlite:///:memory: routes to SQLiteEventStore
- [ ] create_store — error: unsupported URL scheme raises ValueError
- [ ] create_store — error: neither api_key nor database_url raises ValueError
- [ ] LeanLLMContext.merged_labels returns empty dict when all fields are None
- [ ] LeanLLMContext.merged_labels includes typed fields + custom_tags
- [ ] LeanLLMContext.merged_labels — edge: extra kwarg overrides custom_tags
- [ ] ReplayEngine.replay — error: raises NotImplementedError (pending module 5)
- [ ] ReplayEngine.replay_batch — error: raises NotImplementedError (pending module 5)
- [ ] normalize_input — error: raises NotImplementedError (pending module 4)
- [ ] normalize_output — error: raises NotImplementedError (pending module 4)

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
- [ ] non-streaming chat returns the raw LiteLLM response unchanged
- [ ] _classify_error maps timeout-class name to ErrorKind.TIMEOUT
- [ ] _classify_error maps ratelimit to ErrorKind.RATE_LIMIT
- [ ] _classify_error maps json/validation to ErrorKind.PARSING_ERROR
- [ ] _classify_error maps api/provider/connection to ErrorKind.PROVIDER_ERROR
- [ ] _classify_error default falls back to ErrorKind.UNKNOWN
- [ ] _tool_call_to_dict — dict passes through unchanged
- [ ] _tool_call_to_dict — object with model_dump delegates to it
- [ ] _tool_call_to_dict — edge: bare object falls back to {"raw": repr}
- [ ] pre_call_hook receives snapshot with request_id/model/messages
- [ ] post_call_hook fires on success with built LLMEvent
- [ ] post_call_hook does NOT fire on error
- [ ] error_hook fires on exception after error event is enqueued
- [ ] request_id override becomes LLMEvent.event_id
- [ ] correlation_id kwarg is persisted on the event
- [ ] parent_request_id kwarg is persisted on the event
- [ ] parameters whitelist captured (temperature/max_tokens/stream) and unknown kwargs dropped
- [ ] tools kwarg captured on the event
- [ ] tools fallback to "functions" kwarg when tools absent
- [ ] tool_calls captured from response.choices[0].message.tool_calls
- [ ] streaming: wrapper yields all chunks to the caller
- [ ] streaming: ttft_ms and total_stream_time_ms recorded; metadata.stream=True
- [ ] streaming — error: exception during iteration emits error event and re-raises
- [ ] edge: response with no choices leaves content/finish_reason/tool_calls as None
- [ ] edge: missing provider usage triggers estimate_tokens fallback on input and output
- [ ] edge: pre_call_hook raising does not break request path
- [ ] edge: post_call_hook raising does not break request path
- [ ] edge: error_hook raising does not swallow the original exception
- [ ] error: chat re-raises LiteLLM exception after enqueuing error event

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
- [ ] LLMEvent default event_id is a UUID-shaped non-empty string
- [ ] LLMEvent default timestamp is timezone-aware UTC
- [ ] LLMEvent default schema_version equals 1
- [ ] LLMEvent.model_dump(mode='json') serializes timestamp as ISO 8601 string
- [ ] LLMEvent optional fields default to None (prompt, response, tools, tool_calls, error_kind, error_message, time_to_first_token_ms, total_stream_time_ms)
- [ ] LLMEvent.labels and metadata default to empty dicts
- [ ] ErrorKind enum exposes TIMEOUT/RATE_LIMIT/PROVIDER_ERROR/PARSING_ERROR/UNKNOWN values
<!-- pending: parent_request_id / correlation_id / parameters / error-field end-to-end TODO items still [ ] even though LLMEvent surfaces them -->

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
- [ ] CostCalculator.calculate uses exact pricing match for a known model (gpt-4o)
- [ ] CostCalculator.calculate strips provider/model prefix before lookup (openai/gpt-4o)
- [ ] CostCalculator.calculate — edge: versioned name resolves via prefix match (gpt-4o-2024-08-06 → gpt-4o)
- [ ] CostCalculator.calculate — edge: unknown model returns 0.0
- [ ] CostCalculator custom_pricing overrides and adds models
- [ ] CostCalculator.calculate rounds result to 8 decimal places
- [ ] extract_provider picks explicit provider/ prefix when known
- [ ] extract_provider infers provider from base-name prefix (gpt-, claude, gemini, mistral, command)
- [ ] extract_provider — edge: unknown prefix returns "unknown"
- [ ] estimate_tokens returns a positive int for non-empty text
- [ ] estimate_tokens — edge: minimum 1 token returned for empty string
- [ ] estimate_tokens — edge: tiktoken failure falls back to len//4 heuristic

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
- [ ] EventQueue.enqueue returns True when space is available and empty() flips to False
- [ ] EventQueue.drain pulls up to batch_size events in FIFO order
- [ ] EventQueue.drain_all empties the queue
- [ ] EventQueue — edge: enqueue at capacity returns False and increments dropped counter
- [ ] EventWorker drains enqueued events into a fake store batch within 2s
- [ ] EventWorker — edge: retries failing save_batch up to 3 attempts then drops the batch
- [ ] EventWorker flushes remaining events on graceful stop
- [ ] SQLiteEventStore._path_from_url: sqlite:///:memory: → :memory:
- [ ] SQLiteEventStore._path_from_url: sqlite:////abs/path.db → /abs/path.db
- [ ] SQLiteEventStore._path_from_url: sqlite:///./rel.db → ./rel.db
- [ ] SQLiteEventStore.save_batch persists an event (round-trip via SELECT)
- [ ] SQLiteEventStore — edge: save_batch with empty list or before initialize is a no-op
- [ ] SQLiteEventStore — edge: INSERT OR IGNORE makes duplicate event_id idempotent
- [ ] RemoteEventStore.save_batch POSTs /v1/events with bearer auth and body {"events": [...]}
- [ ] RemoteEventStore — edge: save_batch is a no-op on empty list or before initialize
- [ ] RemoteEventStore — edge: service response with dropped>0 emits a warning
- [ ] RemoteEventStore — error: HTTP error status raises (worker retry loop handles it)
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
- [ ] RedactionPolicy.apply FULL mode returns text unchanged
- [ ] RedactionPolicy.apply METADATA_ONLY returns None (capture-less mode)
- [ ] RedactionPolicy.apply REDACTED mode masks emails, phones, CPF, SSN with built-in patterns
- [ ] RedactionPolicy.apply — edge: None input returns None regardless of mode
- [ ] RedactionPolicy.apply — custom patterns applied as compiled regexes, errors swallowed
- [ ] config.redaction_mode=METADATA_ONLY → event.prompt/response are None
- [ ] config.redaction_mode=FULL → event.prompt/response are captured unmasked
- [ ] config.redaction_mode=REDACTED → event.prompt/response are captured then masked (emails→[EMAIL], etc)
- [ ] LeanLLMConfig.from_env reads LEANLLM_REDACTION_MODE and validates enum
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
  - No `environment` / `sampling_rate` / `redaction_mode` fields.
  - No runtime toggles for replay or normalization (they don't exist as modules yet).
  - No per-request `disable_logging` — the only per-call knob is the `labels` dict.
  - No explicit "debug mode" config field (log level is controlled outside by `LEANLLM_LOG_LEVEL` in `cli.py`).
- **Test hooks / seams:**
  - `monkeypatch.setenv("LEANLLM_API_KEY", "x")` + `LeanLLMConfig.from_env()` for env path.
  - Direct constructor for unit tests: `LeanLLMConfig(database_url="sqlite:///:memory:", enable_persistence=True, batch_size=1, flush_interval_ms=20)`.

### Tests

**Target file(s):** `tests/test_config.py`

**Cases to cover:**
- [ ] LeanLLMConfig() defaults match documented values (enable_persistence=True, flush_interval_ms=180_000, batch_size=100, queue_max_size=10_000, auto_migrate=True, capture_content=False)
- [ ] LeanLLMConfig() default endpoint equals https://api.leanllm.dev
- [ ] from_env reads LEANLLM_API_KEY into leanllm_api_key
- [ ] from_env reads LEANLLM_DATABASE_URL into database_url
- [ ] from_env — edge: boolean env accepts "true"/"True"/"TRUE" as True
- [ ] from_env — edge: non-"true" strings ("yes", "1") resolve to False
- [ ] from_env — error: both LEANLLM_DATABASE_URL and LEANLLM_API_KEY set raises ValueError
- [ ] from_env reads LEANLLM_ENDPOINT override
<!-- pending: environment / sampling_rate / redaction_mode fields, per-request disable_logging, debug-mode config -->

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
- [ ] LeanLLM(api_key, config=enable_persistence=False) builds a no-op client (no worker started, _queue is None)
- [ ] LeanLLM without URL/key logs info and leaves _queue/_worker as None
- [ ] Public import surface exposes LeanLLM, LeanLLMConfig, LLMEvent from the leanllm package
- [ ] chat returns the raw LiteLLM response object untouched (library never intercepts the return value)
<!-- pending: leanllm.init(api_key) top-level helper, inspect-last-request debug accessor, CLI view/replay commands -->

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
  - *Replayable*: ❌ replay engine does not exist; parameters and tools are not captured anyway.
  - *Structured*: ✅ `LLMEvent` is Pydantic; JSON-serializable with ISO timestamps.
  - *Doesn't break LiteLLM*: ✅ `chat()` returns the raw `litellm.ModelResponse`.
  - *Pip-installable*: ✅ `pip install leanllm-ai` with `[postgres]` / `[sqlite]` / `[remote]` extras (per `pyproject.toml`).
  - *Minimal setup*: ✅ `LeanLLM(api_key="sk-...")` works out of the box (no persistence unless configured).
  - *Replay any request*: ❌ blocked by modules 1 (parameters/tools capture) and 5 (replay engine).
  - *Trace any chain*: ❌ blocked by modules 2 (context propagation) and 6 (lineage graph).
  - *Data usable for eval/prompt/cost*: ⚠️ partial — cost pipeline is complete; eval/prompt need normalization (module 4) and richer context (module 2).
- **Priorities implied by dependencies:** modules 1 (enrich capture) → 2 (context) → 3 (error field) → 4 (normalization) → 5 (replay) → 6 (lineage). Modules 7 (cost) and 8 (pipeline) are already load-bearing.
