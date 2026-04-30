"""LeanLLM command-line interface.

Usage:
    leanllm migrate {up,down,current,history} [--url URL]
    leanllm logs [--limit N] [--correlation-id ID] [--model M] \\
                 [--since T] [--until T] [--errors-only] [--format table|json]
    leanllm show <event_id> [--pretty]
    leanllm replay <event_id> [--model M] [--temperature T] [--print-diff]
    leanllm replay --batch <file>

Notes:
    `migrate` runs synchronously (Alembic).
    `logs` / `show` / `replay` need a local backend (Postgres or SQLite). The
    CLI does NOT call the SaaS — set LEANLLM_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import logs as _logs
from . import migrate as _migrate
from . import replay as _replay
from . import show as _show


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leanllm")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _migrate.register(sub)
    _logs.register(sub)
    _show.register(sub)
    _replay.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LEANLLM_LOG_LEVEL", "INFO"),
        format="%(levelname)s [%(name)s] %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    func = args.func
    is_async = getattr(args, "_is_async", False)
    if is_async:
        return asyncio.run(func(args))
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
