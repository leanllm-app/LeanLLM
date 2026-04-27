# Claude Code Instructions

> **Read `docs/BACKEND_STANDARDS.md` before every task.** It is the single source of truth for coding patterns in this project.

---

## Workflow

Every task — issue, PR review, prompt — follows these phases.

### 1. Understand

Before writing any code:

- **Read `docs/BACKEND_STANDARDS.md`** — the non-negotiable coding rules.
- **Read the relevant source files** — understand what exists before proposing changes.
- **Check `README.md`** and `pyproject.toml` for the public API surface and dependencies.

### 2. Plan

For non-trivial work (3+ files, architectural decisions, new subsystems):

- Write a brief plan listing:
  - Files that will change
  - New abstractions / boundaries introduced
  - Tests added
  - Risks and edge cases
- Skip planning for trivial changes (config tweaks, single-field additions, typo fixes).

### 3. Implement

- **Branch from `origin/main`**: `git checkout -b <type>/<short-description> origin/main`
- **Commit frequently** — one logical concern per commit.
- Follow every rule in `BACKEND_STANDARDS.md`. No exceptions.
- Keep the public API stable. Breaking changes require a major version bump.

### 4. Test (MANDATORY — never skip)

> **No code change is complete without test updates.** A change without corresponding test updates is an incomplete change.

**After every code change, ask:**

1. **Did I add or change a public function/class?** → A test MUST cover the change.
2. **Did I change logic (conditionals, error paths, business rules)?** → Test cases MUST cover every new/changed branch.
3. **Did I touch the persistence layer or queue?** → Integration tests MUST exercise the full pipeline.

**Run tests:**

```bash
source venv/bin/activate
pytest tests/ -v
```

- Convention: `leanllm/X.py` → `tests/test_X.py`
- Cover: happy path, error cases, edge cases (empty inputs, None values, boundary conditions).
- Both existing and new tests must pass. Never push broken tests.

**Checklist (must all be true before moving to Review):**
- [ ] Every changed module has a corresponding test
- [ ] Every new/changed code branch has at least one test case
- [ ] All tests pass: `pytest tests/ -v`

### 5. Review (Simplicity)

Before committing, audit your own diff for:

1. **Unrequested features** — did you add anything that wasn't asked for?
2. **Unnecessary abstractions** — helpers/utilities used only once?
3. **Over-documentation** — docstrings on obvious methods, comments explaining "what" not "why"?
4. **Premature generalization** — configurable knobs that don't need to be?
5. **Defensive code without a real threat** — try/excepts wrapping safe code, `Optional` on fields that can never be None?

For each "yes": **remove it**. This review is mandatory.

### 6. Verify

**Lint** (required if configured):

```bash
ruff check leanllm/ tests/
ruff format --check leanllm/ tests/
```

If anything fails, fix before committing.

**Type-check** (when configured):

```bash
pyright leanllm/
```

### 7. Complete

- Update `README.md` if the public API or installation steps changed.
- Update `.env.example` if new environment variables were introduced.
- Bump version in `pyproject.toml` and `leanllm/__init__.py` following semver:
  - **patch** (0.x.Y) — bug fixes only
  - **minor** (0.Y.0) — new features, backward-compatible
  - **major** (Y.0.0) — breaking API changes
- Open a PR with a clear summary of the change, motivation, and test coverage.

---

## Quick Reference

```bash
# Activate venv
source venv/bin/activate

# Install (editable + dev deps)
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check leanllm/ tests/
ruff format --check leanllm/ tests/

# Build package
python -m build

# Publish (release only)
twine upload dist/*
```

---

## Git Conventions

- **Branch naming**: `<type>/<short-description>` (e.g., `feat/postgres-store`, `fix/queue-overflow`)
- **Commit format**: `<type>: <description>` (e.g., `feat: add postgres event store`)
- **Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- **Keep commits logical** — one concern per commit.

---

## Key Rules (from BACKEND_STANDARDS.md)

1. Keyword-only arguments (`*`) on all methods with 2+ params.
2. Pydantic for cross-boundary contracts; explicit primitives for internal calls.
3. Units in field names: `_utc`, `_ms`, `_seconds`, `_usd_cents`, `_bytes`.
4. Enums for status/state. Never raw strings.
5. `None` for missing data. No sentinel values (`""`, `0`, `"unknown"`).
6. Try/except is NOT control flow — use explicit conditionals.
7. No `getattr` / `setattr` / `hasattr` for fields you control.
8. Imports at top of file.
9. Storage backends own their own I/O. Clients orchestrate, never touch DB drivers directly.
10. Don't add what wasn't asked for.

---

## Project Conventions

### Public API surface

- `LeanLLM` (client) — sync, never blocks the request thread.
- `LeanLLMConfig` — env-driven configuration.
- `LLMEvent` — versioned (`schema_version`) Pydantic model.
- Anything under `leanllm.events.*` and `leanllm.storage.*` is **internal**. Treat as private; refactors don't require deprecation.

### Two operating modes (mutually exclusive)

- **Self-hosted:** `LEANLLM_DATABASE_URL=postgresql://...` → events stored locally via asyncpg/aiosqlite
- **Remote (SaaS):** `LEANLLM_API_KEY=lllm_xxx` → events batched and POSTed to the LeanLLM Service
- Setting both → `ValueError` at startup
- Setting neither → persistence disabled (warning logged)

### Async / threading boundaries

- The main client is **sync**. Users call `client.chat(...)` from sync code.
- The event worker runs an **asyncio loop in a daemon thread**. Never `await` from the request path.
- Any new storage backend must implement `BaseEventStore` (async) and be safe to run inside the worker thread's loop.

### Flush policy

- **batch_size:** 100 events (default)
- **flush_interval_ms:** 180,000 ms = 3 minutes (default)
- Whichever triggers first. Same for all backends (Postgres, SQLite, Remote).

### Persistence

- New backends go in `leanllm/storage/<name>.py` and inherit `BaseEventStore`.
- Schema changes to `LLMEvent` require bumping `schema_version` and a forward-compatible DDL change in the affected backend.
- Never block on DB I/O from the request path. Period.

### Storage backends

| Backend | Module | Trigger | Extra |
|---|---|---|---|
| `PostgresEventStore` | `storage/postgres.py` | `LEANLLM_DATABASE_URL=postgresql://` | `[postgres]` |
| `SQLiteEventStore` | `storage/sqlite.py` | `LEANLLM_DATABASE_URL=sqlite:///` | `[sqlite]` |
| `RemoteEventStore` | `storage/remote.py` | `LEANLLM_API_KEY=lllm_xxx` | `[remote]` |

### Adding a new model to the cost table

- Edit `leanllm/events/cost.py` `_PRICING` dict.
- Use `(input_usd_per_1M_tokens, output_usd_per_1M_tokens)`.
- The resolver supports prefix matching, so `gpt-4o-2024-08-06` resolves to `gpt-4o` automatically.

---

## When in doubt

- **Smaller is better.** Delete code, don't add it.
- **Explicit beats implicit.** A longer parameter list beats a magic dict.
- **The request path is sacred.** Any feature that introduces blocking on the chat call is a bug.
