"""Unit tests for src/settings.py."""

from __future__ import annotations

import pytest

from src.settings import get_settings


def test_defaults_are_used_when_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: with no env vars set, sane defaults are returned."""
    for name in (
        "TITAN_LOG_LEVEL",
        "TITAN_STORAGE_PATH",
        "TITAN_DOWNLOAD_DELAY",
        "TITAN_RETRY_MAX_ATTEMPTS",
        "TITAN_RETRY_BASE_DELAY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = get_settings()

    assert settings.log_level == "INFO"
    assert settings.storage_path == "data/titan_apex.sqlite3"
    assert settings.default_download_delay == 1.0
    assert settings.retry_max_attempts == 5
    assert settings.retry_base_delay == 1.0


def test_env_overrides_are_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path (override): explicit env vars win over defaults."""
    monkeypatch.setenv("TITAN_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TITAN_RETRY_MAX_ATTEMPTS", "9")
    monkeypatch.setenv("TITAN_DOWNLOAD_DELAY", "2.5")

    settings = get_settings()

    assert settings.log_level == "DEBUG"
    assert settings.retry_max_attempts == 9
    assert settings.default_download_delay == 2.5


def test_invalid_int_env_var_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure case 1: a malformed integer env var fails loudly, not silently."""
    monkeypatch.setenv("TITAN_RETRY_MAX_ATTEMPTS", "not-a-number")

    with pytest.raises(ValueError, match="not-a-number"):
        get_settings()


def test_invalid_float_env_var_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure case 2: a malformed float env var fails loudly, not silently."""
    monkeypatch.setenv("TITAN_DOWNLOAD_DELAY", "definitely-not-a-float")

    with pytest.raises(ValueError, match="definitely-not-a-float"):
        get_settings()
