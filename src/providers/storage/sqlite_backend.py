"""SQLite implementation of the :class:`StorageBackend` interface.

This is the first, simplest storage backend (Phase 1). A future
``postgres_backend.py`` (Phase 4+) implements the exact same interface, so
nothing outside ``src/providers/storage/`` needs to change to swap it in.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.core.exceptions import StorageError
from src.core.interfaces.storage_backend import StorageBackend

_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS items ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
)


class SQLiteStorageBackend(StorageBackend):
    """Stores each item as a JSON blob in a single SQLite table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._closed = False

        parent = Path(db_path).parent
        if str(parent) not in ("", ".") and not parent.exists():
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageError(f"cannot create storage directory: {parent}") from exc

        try:
            self._conn = sqlite3.connect(db_path)
            self._conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise StorageError(f"cannot open sqlite database at {db_path!r}") from exc

        self._init_schema()

    def _init_schema(self) -> None:
        try:
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot initialize sqlite schema at {self._db_path!r}") from exc

    def save(self, item: Mapping[str, Any]) -> None:
        try:
            payload = json.dumps(dict(item))
        except (TypeError, ValueError) as exc:
            raise StorageError(f"item is not JSON-serializable: {item!r}") from exc

        cursor = None
        try:
            cursor = self._conn.cursor()
            cursor.execute("INSERT INTO items (payload) VALUES (?)", (payload,))
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"failed to save item: {item!r}") from exc
        finally:
            if cursor is not None:
                cursor.close()

    def query(self, filters: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        cursor = None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT payload FROM items")
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            raise StorageError("failed to query items") from exc
        finally:
            if cursor is not None:
                cursor.close()

        results: list[Mapping[str, Any]] = []
        for row in rows:
            try:
                payload: dict[str, Any] = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise StorageError("corrupted item payload found in storage") from exc
            if all(payload.get(key) == value for key, value in filters.items()):
                results.append(payload)
        return results

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StorageError("failed to close sqlite connection") from exc
        finally:
            self._closed = True
