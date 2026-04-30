import pytest

from leanllm.storage import (
    PostgresEventStore,
    RemoteEventStore,
    SQLiteEventStore,
    create_store,
)


def test_create_store_api_key_returns_remote_and_wins_over_database_url():
    store = create_store(api_key="lllm_xyz", database_url="sqlite:///./events.db")
    assert isinstance(store, RemoteEventStore)


def test_create_store_postgres_url_returns_postgres():
    store = create_store(database_url="postgresql://user:pass@localhost/db")
    assert isinstance(store, PostgresEventStore)


def test_create_store_sqlite_url_returns_sqlite():
    store = create_store(database_url="sqlite:///./events.db")
    assert isinstance(store, SQLiteEventStore)


def test_create_store_sqlite_memory_url_routes_to_sqlite():
    store = create_store(database_url="sqlite:///:memory:")
    assert isinstance(store, SQLiteEventStore)


def test_create_store_unsupported_scheme_raises():
    with pytest.raises(ValueError, match="Unsupported storage URL scheme"):
        create_store(database_url="mysql://localhost/db")


def test_create_store_neither_api_key_nor_database_url_raises():
    with pytest.raises(ValueError, match="No storage configured"):
        create_store()
