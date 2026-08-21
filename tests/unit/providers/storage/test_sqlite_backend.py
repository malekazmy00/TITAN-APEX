"""Unit tests for src/providers/storage/sqlite_backend.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.exceptions import StorageError
from src.providers.storage.sqlite_backend import SQLiteStorageBackend


def test_save_and_query_roundtrip(tmp_path: Path) -> None:
    """Happy path: an item saved can be found again via query()."""
    backend = SQLiteStorageBackend(str(tmp_path / "sub" / "data.sqlite3"))
    try:
        backend.save({"name": "alice", "age": 30})
        backend.save({"name": "bob", "age": 25})

        results = backend.query({"name": "alice"})

        assert results == [{"name": "alice", "age": 30}]
    finally:
        backend.close()


def test_query_with_no_filters_returns_everything(tmp_path: Path) -> None:
    backend = SQLiteStorageBackend(str(tmp_path / "data.sqlite3"))
    try:
        backend.save({"n": 1})
        backend.save({"n": 2})

        assert len(backend.query({})) == 2
    finally:
        backend.close()


def test_open_database_at_path_occupied_by_a_directory_raises_storage_error(
    tmp_path: Path,
) -> None:
    """Failure case 1: opening a DB where a directory of the same name exists fails loudly."""
    occupied_path = tmp_path / "not_a_file"
    occupied_path.mkdir()

    with pytest.raises(StorageError, match="cannot open sqlite database"):
        SQLiteStorageBackend(str(occupied_path))


def test_save_non_serializable_item_raises_storage_error(tmp_path: Path) -> None:
    """Failure case 2: an item that cannot be JSON-encoded is rejected, not silently dropped."""
    backend = SQLiteStorageBackend(str(tmp_path / "data.sqlite3"))
    try:
        with pytest.raises(StorageError, match="not JSON-serializable"):
            backend.save({"bad": {1, 2, 3}})
    finally:
        backend.close()


def test_query_on_corrupted_payload_raises_storage_error(tmp_path: Path) -> None:
    """Failure case 3: a corrupted row in storage is reported, not silently skipped."""
    backend = SQLiteStorageBackend(str(tmp_path / "data.sqlite3"))
    try:
        backend._conn.execute("INSERT INTO items (payload) VALUES (?)", ("{not valid json",))
        backend._conn.commit()

        with pytest.raises(StorageError, match="corrupted item payload"):
            backend.query({})
    finally:
        backend.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    backend = SQLiteStorageBackend(str(tmp_path / "data.sqlite3"))
    backend.close()
    backend.close()  # must not raise


def test_save_closes_cursor_even_when_execute_fails(tmp_path: Path) -> None:
    """The cursor must be closed in `finally` even when the INSERT itself fails."""
    backend = SQLiteStorageBackend(str(tmp_path / "data.sqlite3"))
    try:
        fake_cursor = MagicMock()
        fake_cursor.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        backend._conn = MagicMock()
        backend._conn.cursor.return_value = fake_cursor

        with pytest.raises(StorageError, match="failed to save item"):
            backend.save({"x": 1})

        fake_cursor.close.assert_called_once()
    finally:
        backend._closed = True  # connection is a mock; nothing real left to close
