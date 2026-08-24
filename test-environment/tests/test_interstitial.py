"""Unit tests for mock-target/structural/interstitial.py."""

from __future__ import annotations

import pytest
from structural.interstitial import (
    INTERSTITIAL_CLOSE_SELECTOR,
    INTERSTITIAL_SELECTOR,
    build_interstitial_feed_page,
    render_interstitial_script,
)

# --- render_interstitial_script ------------------------------------


def test_time_trigger_uses_a_settimeout_with_the_configured_delay() -> None:
    script = render_interstitial_script("time", 1500, 30)

    assert "setTimeout(showInterstitial, 1500);" in script
    assert "window.addEventListener('scroll'" not in script
    assert "pct >=" not in script


def test_scroll_trigger_uses_a_scroll_listener_with_the_configured_threshold() -> None:
    script = render_interstitial_script("scroll", 1500, 42)

    assert "setTimeout(showInterstitial" not in script
    assert "pct >= 42" in script
    assert "window.addEventListener('scroll'" in script


def test_script_wires_the_close_button_to_clear_the_shown_flag() -> None:
    script = render_interstitial_script("time", 1000, 30)

    assert f"document.querySelector('{INTERSTITIAL_CLOSE_SELECTOR}')" in script
    assert "window.__interstitialShown = false;" in script
    assert f"document.querySelector('{INTERSTITIAL_SELECTOR}').style.display = 'none';" in script


def test_rejects_an_unknown_trigger() -> None:
    with pytest.raises(ValueError, match="trigger must be one of"):
        render_interstitial_script("click", 1000, 30)


def test_rejects_a_zero_delay() -> None:
    with pytest.raises(ValueError, match="delay_ms must be > 0"):
        render_interstitial_script("time", 0, 30)


def test_rejects_a_negative_delay() -> None:
    with pytest.raises(ValueError, match="delay_ms must be > 0"):
        render_interstitial_script("time", -1, 30)


def test_rejects_a_zero_scroll_percent() -> None:
    with pytest.raises(ValueError, match=r"scroll_percent must be in \(0, 100\]"):
        render_interstitial_script("scroll", 1000, 0)


def test_rejects_a_scroll_percent_over_100() -> None:
    with pytest.raises(ValueError, match=r"scroll_percent must be in \(0, 100\]"):
        render_interstitial_script("scroll", 1000, 101)


def test_scroll_percent_of_exactly_100_is_allowed() -> None:
    # Doesn't raise -- 100 is a real, meaningful "only at the very bottom".
    render_interstitial_script("scroll", 1000, 100)


# --- build_interstitial_feed_page -----------------------------------


def test_first_batch_has_no_after_cursor() -> None:
    page = build_interstitial_feed_page("seed-1", None, page_size=3, total_batches=2)

    assert len(page.posts) == 3
    assert page.end_cursor == "0"
    assert page.has_next_page is True


def test_last_batch_reports_no_next_page() -> None:
    first = build_interstitial_feed_page("seed-1", None, page_size=3, total_batches=2)

    second = build_interstitial_feed_page(
        "seed-1", first.end_cursor, page_size=3, total_batches=2
    )

    assert len(second.posts) == 3
    assert second.has_next_page is False


def test_same_seed_and_page_yields_the_same_posts() -> None:
    """Deterministic content, same as structural/feed.py's own
    build_feed_page -- a live test can assert exact expected items."""
    first = build_interstitial_feed_page("seed-1", None, page_size=3, total_batches=2)
    second = build_interstitial_feed_page("seed-1", None, page_size=3, total_batches=2)

    assert [p.post_id for p in first.posts] == [p.post_id for p in second.posts]


def test_rejects_a_malformed_cursor() -> None:
    with pytest.raises(ValueError, match="after_cursor is not a valid page cursor"):
        build_interstitial_feed_page("seed-1", "not-a-page", page_size=3, total_batches=2)


def test_rejects_a_zero_page_size() -> None:
    with pytest.raises(ValueError, match="page_size must be > 0"):
        build_interstitial_feed_page("seed-1", None, page_size=0, total_batches=2)


def test_rejects_a_zero_total_batches() -> None:
    with pytest.raises(ValueError, match="total_batches must be > 0"):
        build_interstitial_feed_page("seed-1", None, page_size=3, total_batches=0)
