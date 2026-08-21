"""Unit tests for mock-target/structural/feed.py."""

from __future__ import annotations

import pytest
from structural.feed import MAX_FEED_PAGES, FeedRateLimiter, build_feed_page


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


# --- build_feed_page -------------------------------------------------


def test_first_page_has_no_after_cursor() -> None:
    """Happy path: the first page (after=None) starts at page 0."""
    page = build_feed_page("session-a", after_cursor=None, page_size=5)

    assert len(page.posts) == 5
    assert page.end_cursor == "0"
    assert page.has_next_page is True


def test_following_the_cursor_advances_pages_without_overlap() -> None:
    first = build_feed_page("session-a", after_cursor=None, page_size=5)
    second = build_feed_page("session-a", after_cursor=first.end_cursor, page_size=5)

    first_ids = {p.post_id for p in first.posts}
    second_ids = {p.post_id for p in second.posts}
    assert first_ids.isdisjoint(second_ids)
    assert second.end_cursor == "1"


def test_feed_terminates_at_max_pages() -> None:
    """The feed must be bounded -- has_next_page must go False by MAX_FEED_PAGES,
    otherwise a real crawl following it would never stop."""
    page = build_feed_page("session-a", after_cursor=str(MAX_FEED_PAGES - 2), page_size=5)

    assert page.has_next_page is False


def test_rejects_non_positive_page_size() -> None:
    """Failure case 1: a page of size zero/negative can't be built."""
    with pytest.raises(ValueError, match="page_size must be > 0"):
        build_feed_page("session-a", after_cursor=None, page_size=0)


def test_rejects_malformed_cursor() -> None:
    """Failure case 2: a tampered/garbage cursor must be rejected cleanly, not
    crash with a raw ValueError from int()."""
    with pytest.raises(ValueError, match="not a valid page cursor"):
        build_feed_page("session-a", after_cursor="not-a-number", page_size=5)


# --- FeedRateLimiter ---------------------------------------------------


def test_requests_under_the_threshold_are_allowed() -> None:
    """Happy path: staying under the threshold never gets rate-limited."""
    clock = _FakeClock()
    limiter = FeedRateLimiter(threshold=3, window_seconds=60, clock=clock)

    for _ in range(3):
        result = limiter.check("client-a")
        assert result.allowed is True
        assert result.retry_after_seconds == 0


def test_retry_after_escalates_on_repeated_violations() -> None:
    """Consecutive violations must make Retry-After grow, not stay fixed --
    the 'escalating', not just 'blocking', behaviour real platforms use."""
    clock = _FakeClock()
    limiter = FeedRateLimiter(threshold=1, window_seconds=10, clock=clock)

    limiter.check("client-a")  # allowed (1st request)
    first_violation = limiter.check("client-a")  # over threshold
    second_violation = limiter.check("client-a")

    assert first_violation.allowed is False
    assert second_violation.allowed is False
    assert second_violation.retry_after_seconds > first_violation.retry_after_seconds


def test_old_requests_fall_out_of_the_sliding_window() -> None:
    clock = _FakeClock()
    limiter = FeedRateLimiter(threshold=1, window_seconds=10, clock=clock)

    limiter.check("client-a")
    clock.now += 11  # window has fully elapsed
    result = limiter.check("client-a")

    assert result.allowed is True


def test_different_clients_are_tracked_independently() -> None:
    clock = _FakeClock()
    limiter = FeedRateLimiter(threshold=1, window_seconds=60, clock=clock)

    limiter.check("client-a")
    limiter.check("client-a")  # over threshold for client-a
    result_b = limiter.check("client-b")

    assert result_b.allowed is True


def test_rejects_non_positive_threshold() -> None:
    """Failure case 3: a threshold of zero/negative can never allow anything."""
    with pytest.raises(ValueError, match="threshold must be > 0"):
        FeedRateLimiter(threshold=0, window_seconds=60)


def test_rejects_non_positive_window() -> None:
    """Failure case 4: a window of zero/negative seconds is meaningless."""
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        FeedRateLimiter(threshold=5, window_seconds=0)


def test_check_rejects_empty_client_key() -> None:
    """Failure case 5: nothing to rate-limit without a client identity."""
    limiter = FeedRateLimiter(threshold=5, window_seconds=60)

    with pytest.raises(ValueError, match="client_key must be non-empty"):
        limiter.check("")
