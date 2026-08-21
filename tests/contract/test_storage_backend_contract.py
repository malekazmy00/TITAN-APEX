"""Contract test suite for StorageBackend implementations.

Every provider implementing the ``StorageBackend`` interface must pass this
suite before it is accepted into the project (docs/REQUIREMENTS.md,
sections 1 & 4). Currently exercised against ``SQLiteStorageBackend``; a
future ``postgres_backend.py`` must pass the same suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.core.interfaces.storage_backend import StorageBackend
from src.providers.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[StorageBackend]:
    instance = SQLiteStorageBackend(str(tmp_path / "contract.sqlite3"))
    try:
        yield instance
    finally:
        instance.close()


def test_is_a_storage_backend(backend: StorageBackend) -> None:
    assert isinstance(backend, StorageBackend)


def test_save_then_query_returns_the_saved_item(backend: StorageBackend) -> None:
    backend.save({"a": 1, "b": "x"})
    backend.save({"a": 2, "b": "y"})

    results = backend.query({"a": 1})

    assert list(results) == [{"a": 1, "b": "x"}]


def test_query_with_empty_filters_returns_every_item(backend: StorageBackend) -> None:
    backend.save({"a": 1})
    backend.save({"a": 2})

    assert len(backend.query({})) == 2


def test_query_with_no_matches_returns_empty_sequence(backend: StorageBackend) -> None:
    backend.save({"a": 1})

    assert list(backend.query({"a": 999})) == []


def test_close_is_idempotent(backend: StorageBackend) -> None:
    backend.close()
    backend.close()
