"""Environment-driven configuration for the mock-target test app.

Mirrors src/settings.py's pattern (read on demand, not at import time, so
tests can freely set environment variables first) but stays fully
independent of src/ -- this is test *infrastructure*, not a dependency of
the product it tests. Every challenge layer is individually toggleable
(test-environment/README.md documents each), per docs/REQUIREMENTS.md
section 8's Escalation Cycle: one layer at a time, each verifiable alone.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    return int(value)


class MockTargetConfig:
    """Snapshot of environment-driven mock-target configuration."""

    def __init__(self) -> None:
        self.enable_botd: bool = _env_bool("ENABLE_BOTD", True)
        self.enable_honeypots: bool = _env_bool("ENABLE_HONEYPOTS", True)
        self.enable_decoy_data: bool = _env_bool("ENABLE_DECOY_DATA", True)
        self.enable_markup_randomizer: bool = _env_bool("ENABLE_MARKUP_RANDOMIZER", True)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's cookie-consent-wall
        # round: gates real content server-side (not a CSS overlay) until
        # a consent cookie is present -- see structural/cookie_wall.py.
        self.enable_cookie_wall: bool = _env_bool("ENABLE_COOKIE_WALL", True)
        self.markup_randomizer_interval_minutes: int = _env_int(
            "MARKUP_RANDOMIZER_INTERVAL_MINUTES", 15
        )
        self.feed_rate_limit_threshold: int = _env_int("FEED_RATE_LIMIT_THRESHOLD", 20)
        self.feed_rate_limit_window_seconds: int = _env_int("FEED_RATE_LIMIT_WINDOW_SECONDS", 60)
        self.feed_page_size: int = _env_int("FEED_PAGE_SIZE", 10)
        self.honeypot_log_path: str = os.environ.get(
            "HONEYPOT_LOG_PATH", "test-environment/mock-target/security/honeypot_triggers.log"
        )
        self.botd_log_path: str = os.environ.get(
            "BOTD_LOG_PATH", "test-environment/mock-target/security/botd_flags.log"
        )


def get_config() -> MockTargetConfig:
    """Build a fresh :class:`MockTargetConfig` snapshot from the current environment."""
    return MockTargetConfig()
