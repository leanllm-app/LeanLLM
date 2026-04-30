"""`leanllm replay <event_id>` — re-run a stored event through the live LLM."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from ..client import LeanLLM
from ..config import LeanLLMConfig
from ..events.models import LLMEvent
from ..replay import ReplayEngine, ReplayOverrides
from ._store import open_store


def _build_overrides(args: argparse.Namespace) -> Optional[ReplayOverrides]:
    """Build ReplayOverrides from CLI flags. Returns None if no override applies."""
    parameters: Optional[Dict[str, Any]] = None
    if args.temperature is not None:
        parameters = {"temperature": args.temperature}
    if args.model or parameters is not None:
        return ReplayOverrides(model=args.model, parameters=parameters)
    return None


def _build_replay_client() -> LeanLLM:
    """Construct a non-persisting client for the actual provider call.

    The replayed event was already persisted once by the original run; we don't
    want a duplicate. So `enable_persistence=False`.

    Provider credentials come from the env (LiteLLM reads OPENAI_API_KEY etc.
    natively). Pass an empty string to LeanLLM — LiteLLM will pick up the right
    one based on the model.
    """
    return LeanLLM(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        config=LeanLLMConfig(enable_persistence=False),
    )


async def cmd_replay(args: argparse.Namespace) -> int:
    store = await open_store(url_arg=args.url)
    try:
        if args.batch:
            event_ids = _read_batch_file(args.batch)
            return await _replay_batch(
                store=store,
                event_ids=event_ids,
                overrides=_build_overrides(args),
                print_diff=args.print_diff,
            )

        if not args.event_id:
            sys.stderr.write("Error: provide an event_id or --batch <file>.\n")
            return 2

        event = await store.get_event(event_id=args.event_id)
        if event is None:
            sys.stderr.write(f"Event {args.event_id} not found.\n")
            return 1

        client = _build_replay_client()
        try:
            engine = ReplayEngine(client=client)
            result = engine.replay(event=event, overrides=_build_overrides(args))
        finally:
            if client._worker is not None:
                client._worker.stop(timeout=2.0)

        if args.print_diff:
            result.pretty_print()
        else:
            print(result.summary())
        return 0
    finally:
        await store.close()


async def _replay_batch(
    *,
    store,
    event_ids: List[str],
    overrides: Optional[ReplayOverrides],
    print_diff: bool,
) -> int:
    events: List[LLMEvent] = []
    missing: List[str] = []
    for eid in event_ids:
        ev = await store.get_event(event_id=eid)
        if ev is None:
            missing.append(eid)
        else:
            events.append(ev)
    if missing:
        sys.stderr.write(
            f"Warning: {len(missing)} event(s) not found, skipped: "
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}\n"
        )
    if not events:
        sys.stderr.write("Error: no events to replay.\n")
        return 1

    client = _build_replay_client()
    try:
        engine = ReplayEngine(client=client)
        results = engine.replay_batch(events=events, overrides=overrides)
    finally:
        if client._worker is not None:
            client._worker.stop(timeout=2.0)

    successes = [r for r in results if r.error_message is None]
    failures = [r for r in results if r.error_message is not None]
    n_text_diffs = sum(1 for r in successes if not r.text_identical)
    total_token_delta = sum(r.tokens_delta for r in successes)
    total_latency_delta = sum(r.latency_ms_delta for r in successes)

    if print_diff:
        for r in results:
            r.pretty_print()
            print()
    else:
        for r in results:
            print(r.summary())

    print()
    print(
        f"Batch summary: replays={len(results)}  "
        f"errors={len(failures)}  text_diffs={n_text_diffs}  "
        f"total_token_delta={total_token_delta:+d}  "
        f"total_latency_delta={total_latency_delta:+d}ms"
    )
    return 0 if not failures else 1


def _read_batch_file(path: str) -> List[str]:
    """Read one event_id per line. Blank lines and `#` comments are ignored."""
    with open(path, "r", encoding="utf-8") as fh:
        ids: List[str] = []
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            ids.append(stripped)
    return ids


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "replay", help="Re-run a stored event through the live LLM"
    )
    p.add_argument("event_id", nargs="?", help="Event id (omit when using --batch)")
    p.add_argument("--url", help="Database URL (overrides LEANLLM_DATABASE_URL)")
    p.add_argument("--model", help="Override the model used in the original event")
    p.add_argument(
        "--temperature", type=float, help="Override the temperature parameter"
    )
    p.add_argument(
        "--batch",
        help="Path to a file with one event_id per line (# for comments). "
        "Triggers batch replay.",
    )
    p.add_argument(
        "--print-diff",
        dest="print_diff",
        action="store_true",
        help="Print full pretty_print() with unified diff (default: one-line summary).",
    )
    p.set_defaults(func=cmd_replay, _is_async=True)
