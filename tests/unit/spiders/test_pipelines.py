"""Unit tests for src/spiders/pipelines.py."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from src.core.exceptions import StorageError
from src.core.interfaces.storage_backend import StorageBackend
from src.spiders.pipelines import StorageBackendPipeline


class _FakeBackend(StorageBackend):
    """In-memory StorageBackend double, with an injectable failure mode."""

    def __init__(self, fail_on_save: bool = False) -> None:
        self.saved: list[dict[str, Any]] = []
        self.closed = False
        self._fail_on_save = fail_on_save

    def save(self, item: Mapping[str, Any]) -> None:
        if self._fail_on_save:
            raise StorageError("simulated backend failure")
        self.saved.append(dict(item))

    def query(self, filters: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return self.saved

    def close(self) -> None:
        self.closed = True


def test_process_item_saves_through_the_backend_and_returns_the_item() -> None:
    """Happy path: the item is persisted via the backend and passed through unchanged."""
    backend = _FakeBackend()
    pipeline = StorageBackendPipeline(backend=backend)

    result = pipeline.process_item({"text": "hello"}, spider=object())

    assert result == {"text": "hello"}
    assert backend.saved == [{"text": "hello"}]


def test_process_item_reraises_storage_error_instead_of_swallowing_it() -> None:
    """Failure case 1: a StorageError from the backend propagates, never silently dropped."""
    backend = _FakeBackend(fail_on_save=True)
    pipeline = StorageBackendPipeline(backend=backend)

    with pytest.raises(StorageError, match="simulated backend failure"):
        pipeline.process_item({"text": "hello"}, spider=object())


def test_close_spider_closes_the_backend() -> None:
    """Failure-adjacent case: close_spider must release the backend even on a fresh pipeline."""
    backend = _FakeBackend()
    pipeline = StorageBackendPipeline(backend=backend)

    pipeline.close_spider(spider=object())

    assert backend.closed is True


def test_from_crawler_falls_back_to_settings_storage_path(tmp_path: Any) -> None:
    """Failure case 2: an unreachable storage path surfaces as StorageError, not a crash."""

    class _FakeSettings:
        def get(self, name: str, default: Any = None) -> Any:
            occupied_dir = tmp_path / "occupied"
            occupied_dir.mkdir()
            return {"TITAN_STORAGE_PATH": str(occupied_dir)}.get(name, default)

    class _FakeCrawler:
        settings = _FakeSettings()

    with pytest.raises(StorageError, match="cannot open sqlite database"):
        StorageBackendPipeline.from_crawler(_FakeCrawler())
