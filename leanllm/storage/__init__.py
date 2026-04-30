from urllib.parse import urlparse

from .base import BaseEventStore
from .postgres import PostgresEventStore
from .remote import RemoteEventStore
from .sqlite import SQLiteEventStore

__all__ = [
    "BaseEventStore",
    "PostgresEventStore",
    "RemoteEventStore",
    "SQLiteEventStore",
    "create_store",
]


def create_store(
    *,
    database_url: str | None = None,
    api_key: str | None = None,
    endpoint: str = "https://api.leanllm.dev",
    auto_migrate: bool = True,
) -> BaseEventStore:
    """
    Factory: pick the right backend.

    Priority:
        api_key set        → RemoteEventStore (POST to service)
        database_url set   → PostgresEventStore / SQLiteEventStore (direct insert)
    """
    if api_key:
        return RemoteEventStore(api_key=api_key, endpoint=endpoint)

    if database_url:
        scheme = urlparse(database_url).scheme.lower()

        if scheme.startswith("postgres"):
            return PostgresEventStore(
                database_url=database_url, auto_migrate=auto_migrate
            )

        if scheme == "sqlite":
            return SQLiteEventStore(database_url=database_url)

        raise ValueError(
            f"Unsupported storage URL scheme '{scheme}'. "
            f"Supported: postgresql://, sqlite://"
        )

    raise ValueError(
        "No storage configured. Set LEANLLM_DATABASE_URL or LEANLLM_API_KEY."
    )
