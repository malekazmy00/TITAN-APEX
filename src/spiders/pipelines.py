"""Scrapy item pipeline: persists every scraped item via a StorageBackend.

The pipeline depends only on the ``StorageBackend`` interface
(docs/REQUIREMENTS.md, section 1) — swapping the concrete backend later
(e.g. a future PostgreSQL provider) means changing settings, not this
file.
"""

from __future__ import annotations

from typing import Any

from src.core.exceptions import StorageError
from src.core.interfaces.storage_backend import StorageBackend
from src.logging_config import get_logger
from src.providers.storage.sqlite_backend import SQLiteStorageBackend
from src.settings import get_settings


class StorageBackendPipeline:
    """Saves every item yielded by a spider through a ``StorageBackend``."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self.logger = get_logger(__name__)

    @classmethod
    def from_crawler(cls, crawler: Any) -> StorageBackendPipeline:
        storage_path = crawler.settings.get("TITAN_STORAGE_PATH") or get_settings().storage_path
        return cls(backend=SQLiteStorageBackend(str(storage_path)))

    def process_item(self, item: dict[str, Any], spider: Any) -> dict[str, Any]:
        try:
            self._backend.save(item)
        except StorageError:
            self.logger.error("storage_pipeline.save_failed", extra={"item": item})
            raise
        return item

    def close_spider(self, spider: Any) -> None:
        self._backend.close()
