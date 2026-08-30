"""Unit tests for src/core/exceptions.py."""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    AIAnalyzerError,
    AntibotError,
    BrowserCrashedError,
    ConfigError,
    SpiderError,
    StorageError,
    TitanApexError,
)


def test_all_project_exceptions_are_titan_apex_errors() -> None:
    """Happy path: every specific exception subclasses the common base."""
    for exc_type in (
        ConfigError,
        SpiderError,
        StorageError,
        AntibotError,
        AIAnalyzerError,
        BrowserCrashedError,
    ):
        assert issubclass(exc_type, TitanApexError)


def test_browser_crashed_error_is_also_catchable_as_antibot_error() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17: unlike the sibling
    exception types above, BrowserCrashedError is deliberately a
    *subclass* of AntibotError, not a sibling -- every existing
    ``except AntibotError`` catch site must keep working unchanged for
    it, while a caller that wants to distinguish it specifically still
    can (catch BrowserCrashedError before the broader AntibotError)."""
    with pytest.raises(AntibotError):
        raise BrowserCrashedError("camoufox's browser engine crashed mid-solve")


def test_config_error_can_be_raised_and_caught_specifically() -> None:
    """Failure case 1: a ConfigError is catchable without a bare except."""
    with pytest.raises(ConfigError, match="bad config"):
        raise ConfigError("bad config")


def test_storage_error_is_not_caught_as_a_different_sibling_type() -> None:
    """Failure case 2: sibling exception types stay distinct (not aliases)."""
    with pytest.raises(StorageError):
        try:
            raise StorageError("disk full")
        except AntibotError:  # pragma: no cover - must never match
            pytest.fail("StorageError must not be caught as AntibotError")
