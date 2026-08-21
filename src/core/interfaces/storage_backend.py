"""Contract for storage backend providers (SQLite, PostgreSQL, ...).

This module defines the abstract contract only. See
``src/providers/storage/`` for concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class StorageBackend(ABC):
    """Abstract contract every storage backend implementation must follow.

    Implementations must raise
    :class:`src.core.exceptions.StorageError` (never a bare
    ``Exception``) on failure, and must release any external resource
    (DB connection, cursor, file handle, ...) in a ``finally`` block, both
    inside individual methods and via :meth:`close`.
    """

    @abstractmethod
    def save(self, item: Mapping[str, Any]) -> None:
        """Persist a single scraped item.

        Raises:
            src.core.exceptions.StorageError: if the item cannot be
                persisted (e.g. connection lost, item not serializable).
        """
        raise NotImplementedError

    @abstractmethod
    def query(self, filters: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Return every stored item matching ``filters`` (equality match).

        Raises:
            src.core.exceptions.StorageError: if the query cannot be
                executed.
        """
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release the underlying resource. Must be idempotent."""
        raise NotImplementedError
