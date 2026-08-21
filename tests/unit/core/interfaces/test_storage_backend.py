"""Unit tests for src/core/interfaces/storage_backend.py."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from src.core.exceptions import StorageError
from src.core.interfaces.storage_backend import StorageBackend


class _InMemoryStorageBackend(StorageBackend):
    """Minimal concrete implementation used only to exercise the contract."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._closed = False

    def save(self, item: Mapping[str, Any]) -> None:
        if self._closed:
            raise StorageError("backend is closed")
        self._items.append(dict(item))

    def query(self, filters: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return [i for i in self._items if all(i.get(k) == v for k, v in filters.items())]

    def close(self) -> None:
        self._closed = True


def test_concrete_implementation_satisfies_the_contract() -> None:
    """Happy path: save then query round-trips an item."""
    backend: StorageBackend = _InMemoryStorageBackend()
    backend.save({"name": "alice"})
    assert backend.query({"name": "alice"}) == [{"name": "alice"}]
    backend.close()


def test_abstract_class_cannot_be_instantiated_directly() -> None:
    """Failure case 1: the ABC itself is not instantiable."""
    with pytest.raises(TypeError):
        StorageBackend()  # type: ignore[abstract]


def test_partial_implementation_cannot_be_instantiated() -> None:
    """Failure case 2: a subclass missing an abstract method stays abstract."""

    class _Incomplete(StorageBackend):
        def save(self, item: Mapping[str, Any]) -> None:
            return None

        # query() and close() intentionally not implemented.

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]
