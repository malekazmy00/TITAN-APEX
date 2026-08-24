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
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's A/B-variant round:
        # the post container tag varies between requests -- see
        # structural/ab_variant.py.
        self.enable_ab_variants: bool = _env_bool("ENABLE_AB_VARIANTS", True)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's placeholder-content
        # round: post text renders as a literal "Loading..." placeholder,
        # swapped for the real text by client-side JS after this delay --
        # see structural/placeholder_content.py.
        self.enable_placeholder_content: bool = _env_bool("ENABLE_PLACEHOLDER_CONTENT", True)
        self.placeholder_delay_ms: int = _env_int("PLACEHOLDER_DELAY_MS", 500)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Shadow DOM round
        # (محور 3): every other post renders inside a real, client-side
        # -attached shadow root instead of plain light-DOM markup -- see
        # structural/shadow_dom.py.
        self.enable_shadow_dom: bool = _env_bool("ENABLE_SHADOW_DOM", True)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's DOM Virtualization
        # round (محور 3): /feed keeps only a bounded window of posts
        # genuinely present in the DOM at once, evicting the oldest as new
        # ones load -- see structural/dom_virtualization.py.
        self.enable_dom_virtualization: bool = _env_bool("ENABLE_DOM_VIRTUALIZATION", True)
        self.dom_virtualization_window_size: int = _env_int("DOM_VIRTUALIZATION_WINDOW_SIZE", 5)
        self.markup_randomizer_interval_minutes: int = _env_int(
            "MARKUP_RANDOMIZER_INTERVAL_MINUTES", 15
        )
        self.feed_rate_limit_threshold: int = _env_int("FEED_RATE_LIMIT_THRESHOLD", 20)
        self.feed_rate_limit_window_seconds: int = _env_int("FEED_RATE_LIMIT_WINDOW_SECONDS", 60)
        self.feed_page_size: int = _env_int("FEED_PAGE_SIZE", 10)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Known Limitation
        # #1 (login/session, activated ahead of Interstitials per
        # explicit user request): real server-side session TTL -- see
        # security/auth.py's SessionStore.
        self.session_ttl_seconds: int = _env_int("SESSION_TTL_SECONDS", 300)
        # /feed-protected's own page size/total-pages -- deliberately
        # small and fixed (unlike /feed's DOM-virtualized version), since
        # this round is about login/session mechanics, not about
        # re-testing lazy-loading/virtualization on top of them.
        self.protected_feed_page_size: int = _env_int("PROTECTED_FEED_PAGE_SIZE", 5)
        self.protected_feed_total_pages: int = _env_int("PROTECTED_FEED_TOTAL_PAGES", 2)
        # docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Interstitials round
        # (محور 6): a full-screen overlay on /feed-interstitial that
        # genuinely blocks further loading (not just the view) until
        # dismissed -- see structural/interstitial.py. "time"/"scroll"
        # trigger; delay/threshold and page geometry are separately
        # configurable so the mock stays deterministic for live CI (real
        # elapsed time, not a randomized threshold).
        self.interstitial_trigger: str = os.environ.get("INTERSTITIAL_TRIGGER", "time")
        self.interstitial_delay_ms: int = _env_int("INTERSTITIAL_DELAY_MS", 1000)
        self.interstitial_scroll_percent: int = _env_int("INTERSTITIAL_SCROLL_PERCENT", 30)
        self.interstitial_feed_page_size: int = _env_int("INTERSTITIAL_FEED_PAGE_SIZE", 5)
        self.interstitial_feed_total_batches: int = _env_int(
            "INTERSTITIAL_FEED_TOTAL_BATCHES", 3
        )
        self.honeypot_log_path: str = os.environ.get(
            "HONEYPOT_LOG_PATH", "test-environment/mock-target/security/honeypot_triggers.log"
        )
        self.botd_log_path: str = os.environ.get(
            "BOTD_LOG_PATH", "test-environment/mock-target/security/botd_flags.log"
        )


def get_config() -> MockTargetConfig:
    """Build a fresh :class:`MockTargetConfig` snapshot from the current environment."""
    return MockTargetConfig()
