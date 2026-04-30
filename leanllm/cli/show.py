"""`leanllm show <event_id>` — single-event detail."""

from __future__ import annotations

import argparse
import sys

from ._store import open_store


async def cmd_show(args: argparse.Namespace) -> int:
    store = await open_store(url_arg=args.url)
    try:
        event = await store.get_event(event_id=args.event_id)
        if event is None:
            sys.stderr.write(f"Event {args.event_id} not found.\n")
            return 1
        if args.pretty:
            event.pretty_print()
        else:
            print(event.model_dump_json(indent=2))
        return 0
    finally:
        await store.close()


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("show", help="Show full detail for a single event")
    p.add_argument("event_id", help="Event id to fetch")
    p.add_argument("--url", help="Database URL (overrides LEANLLM_DATABASE_URL)")
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Use sectioned pretty_print() instead of raw JSON",
    )
    p.set_defaults(func=cmd_show, _is_async=True)
