"""Migration subcommands — `leanllm migrate {up,down,current,history}`.

Sync (Alembic-driven). Kept separate from the async query/replay subcommands.
"""

from __future__ import annotations

import argparse
import sys

from ..config import LeanLLMConfig


def _resolve_url(arg_url: str | None) -> str:
    url = arg_url or LeanLLMConfig.from_env().database_url
    if not url:
        sys.stderr.write(
            "Error: no database URL provided.\n"
            "Pass --url or set LEANLLM_DATABASE_URL.\n"
        )
        sys.exit(2)
    return url


def cmd_up(args: argparse.Namespace) -> int:
    from ..storage.migrations.runner import upgrade_postgres

    upgrade_postgres(url=_resolve_url(args.url), revision=args.rev)
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    from ..storage.migrations.runner import downgrade_postgres

    downgrade_postgres(url=_resolve_url(args.url), revision=args.rev)
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    from ..storage.migrations.runner import current_postgres

    rev = current_postgres(url=_resolve_url(args.url))
    print(rev or "<no migrations applied>")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from ..storage.migrations.runner import history_postgres

    history_postgres(url=_resolve_url(args.url))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    migrate = subparsers.add_parser("migrate", help="Manage database schema migrations")
    migrate_sub = migrate.add_subparsers(dest="action", required=True)

    up = migrate_sub.add_parser("up", help="Apply pending migrations")
    up.add_argument("--url", help="Database URL (overrides LEANLLM_DATABASE_URL)")
    up.add_argument("--rev", default="head", help="Target revision (default: head)")
    up.set_defaults(func=cmd_up, _is_async=False)

    down = migrate_sub.add_parser("down", help="Roll back migrations")
    down.add_argument("--url", help="Database URL")
    down.add_argument(
        "--rev", default="-1", help="Target revision (default: one step back)"
    )
    down.set_defaults(func=cmd_down, _is_async=False)

    current = migrate_sub.add_parser("current", help="Show the current schema revision")
    current.add_argument("--url", help="Database URL")
    current.set_defaults(func=cmd_current, _is_async=False)

    history = migrate_sub.add_parser("history", help="Show migration history")
    history.add_argument("--url", help="Database URL")
    history.set_defaults(func=cmd_history, _is_async=False)
