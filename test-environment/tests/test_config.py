"""Unit tests for mock-target/config.py."""

from __future__ import annotations

import pytest
from config import get_config


def test_defaults_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: every layer defaults to enabled, sane numeric defaults."""
    for name in (
        "ENABLE_BOTD",
        "ENABLE_HONEYPOTS",
        "ENABLE_DECOY_DATA",
        "ENABLE_MARKUP_RANDOMIZER",
        "MARKUP_RANDOMIZER_INTERVAL_MINUTES",
        "FEED_RATE_LIMIT_THRESHOLD",
        "FEED_RATE_LIMIT_WINDOW_SECONDS",
        "FEED_PAGE_SIZE",
        "ENABLE_COOKIE_WALL",
        "ENABLE_AB_VARIANTS",
        "ENABLE_PLACEHOLDER_CONTENT",
        "PLACEHOLDER_DELAY_MS",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = get_config()

    assert cfg.enable_botd is True
    assert cfg.enable_honeypots is True
    assert cfg.enable_decoy_data is True
    assert cfg.enable_markup_randomizer is True
    assert cfg.markup_randomizer_interval_minutes == 15
    assert cfg.feed_rate_limit_threshold == 20
    assert cfg.feed_rate_limit_window_seconds == 60
    assert cfg.feed_page_size == 10
    assert cfg.enable_cookie_wall is True
    assert cfg.enable_ab_variants is True
    assert cfg.enable_placeholder_content is True
    assert cfg.placeholder_delay_ms == 500


def test_each_layer_individually_toggleable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-adjacent case 1: a falsy-string toggle must actually disable, not
    just "any string is truthy" (a common env-var parsing bug)."""
    monkeypatch.setenv("ENABLE_BOTD", "false")
    monkeypatch.setenv("ENABLE_HONEYPOTS", "0")
    monkeypatch.setenv("ENABLE_DECOY_DATA", "no")
    monkeypatch.setenv("ENABLE_MARKUP_RANDOMIZER", "true")
    monkeypatch.setenv("ENABLE_COOKIE_WALL", "false")
    monkeypatch.setenv("ENABLE_AB_VARIANTS", "0")
    monkeypatch.setenv("ENABLE_PLACEHOLDER_CONTENT", "no")

    cfg = get_config()

    assert cfg.enable_botd is False
    assert cfg.enable_honeypots is False
    assert cfg.enable_decoy_data is False
    assert cfg.enable_markup_randomizer is True
    assert cfg.enable_cookie_wall is False
    assert cfg.enable_ab_variants is False
    assert cfg.enable_placeholder_content is False


def test_numeric_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-adjacent case 2: numeric overrides must actually be applied, not
    silently ignored in favour of the default."""
    monkeypatch.setenv("MARKUP_RANDOMIZER_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("FEED_RATE_LIMIT_THRESHOLD", "50")
    monkeypatch.setenv("PLACEHOLDER_DELAY_MS", "1200")

    cfg = get_config()

    assert cfg.markup_randomizer_interval_minutes == 5
    assert cfg.feed_rate_limit_threshold == 50
    assert cfg.placeholder_delay_ms == 1200
