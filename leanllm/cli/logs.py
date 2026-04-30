"""`leanllm logs` — list recent events as ASCII table or JSONL."""

from __future__ import annotations

import argparse
import json
from typing import List, Sequence

from ..events.models import LLMEvent
from ._store import open_store, parse_when


_TABLE_COLUMNS = (
    ("event_id", 36),
    ("timestamp", 19),
    ("model", 22),
    ("latency_ms", 10),
    ("tokens", 10),
    ("cost", 10),
    ("error_kind", 14),
)


def _format_table(events: Sequence[LLMEvent]) -> str:
    lines: List[str] = []
    header = " | ".join(name.ljust(width) for name, width in _TABLE_COLUMNS)
    sep = "-+-".join("-" * width for _, width in _TABLE_COLUMNS)
    lines.append(header)
    lines.append(sep)
    for ev in events:
        cells = [
            ev.event_id[:36].ljust(36),
            ev.timestamp.strftime("%Y-%m-%d %H:%M:%S")[:19].ljust(19),
            ev.model[:22].ljust(22),
            str(ev.latency_ms)[:10].ljust(10),
            f"{ev.input_tokens}/{ev.output_tokens}"[:10].ljust(10),
            f"${ev.cost:.4f}"[:10].ljust(10),
            (ev.error_kind.value if ev.error_kind else "")[:14].ljust(14),
        ]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _format_jsonl(events: Sequence[LLMEvent]) -> str:
    return "\n".join(json.dumps(ev.model_dump(mode="json")) for ev in events)


async def cmd_logs(args: argparse.Namespace) -> int:
    store = await open_store(url_arg=args.url)
    try:
        since = parse_when(args.since) if args.since else None
        until = parse_when(args.until) if args.until else None
        events = await store.list_events(
            correlation_id=args.correlation_id,
            model=args.model,
            since=since,
            until=until,
            errors_only=args.errors_only,
            limit=args.limit,
            offset=args.offset,
        )
        if args.format == "json":
            output = _format_jsonl(events)
        else:
            output = _format_table(events)
        if output:
            print(output)
        return 0
    finally:
        await store.close()


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("logs", help="List recent LLM events")
    p.add_argument("--url", help="Database URL (overrides LEANLLM_DATABASE_URL)")
    p.add_argument("--limit", type=int, default=20, help="Max rows (default: 20)")
    p.add_argument(
        "--offset", type=int, default=0, help="Pagination offset (default: 0)"
    )
    p.add_argument(
        "--correlation-id", dest="correlation_id", help="Filter by correlation_id"
    )
    p.add_argument("--model", help="Filter by model name (exact match)")
    p.add_argument(
        "--since",
        help="Start of time window. ISO-8601 (2026-04-27 / 2026-04-27T10:00:00) or relative ('1h', '30m', '2d').",
    )
    p.add_argument("--until", help="End of time window. Same formats as --since.")
    p.add_argument(
        "--errors-only",
        dest="errors_only",
        action="store_true",
        help="Only events where error_kind IS NOT NULL",
    )
    p.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table)",
    )
    p.set_defaults(func=cmd_logs, _is_async=True)
