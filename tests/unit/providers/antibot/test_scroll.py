"""Unit tests for src/providers/antibot/_scroll.py.

A fake Page tracks .evaluate()/.wait_for_timeout() calls and returns a
scripted sequence of scrollHeight values -- no real browser involved.
"""

from __future__ import annotations

import pytest

from src.providers.antibot._scroll import scroll_to_load_lazy_content


class _FakePage:
    def __init__(self, heights: list[int]) -> None:
        # heights[0] is the initial height (read once before the loop);
        # heights[1:] are what each subsequent "after scroll" read returns.
        self._heights = iter(heights)
        self.evaluate_calls: list[str] = []
        self.wait_calls: list[int] = []

    def evaluate(self, script: str) -> int | None:
        self.evaluate_calls.append(script)
        if script == "document.body.scrollHeight":
            return next(self._heights)
        return None  # window.scrollTo(...)'s return value is never read

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls.append(ms)


def test_stops_after_one_attempt_when_height_never_grows() -> None:
    """Happy path (the common, "no infinite scroll here" case): height
    stays flat, so exactly one scroll+wait round trip happens, not
    max_attempts of them -- the harmless-no-op justification this module
    exists to provide."""
    page = _FakePage([1000, 1000])  # initial read, then one post-scroll read

    scroll_to_load_lazy_content(page, max_attempts=8, pause_ms=700)

    assert len(page.wait_calls) == 1
    assert page.wait_calls == [700]


def test_keeps_scrolling_while_height_grows_then_stops() -> None:
    """Happy path: a genuinely lazy-loading page keeps scrolling as long
    as new content keeps arriving, and stops the moment it doesn't."""
    # initial=1000, then grows twice (2000, 3000), then stops (3000 again)
    page = _FakePage([1000, 2000, 3000, 3000])

    scroll_to_load_lazy_content(page, max_attempts=8, pause_ms=700)

    assert len(page.wait_calls) == 3


def test_respects_max_attempts_even_if_height_keeps_growing() -> None:
    """Failure-adjacent case 1: a page that never stabilizes (or a
    scrollHeight read that's flaky) must not loop forever -- bounded by
    max_attempts, matching render_with_playwright's identical contract."""
    ever_growing = [1000 * (i + 1) for i in range(20)]
    page = _FakePage(ever_growing)

    scroll_to_load_lazy_content(page, max_attempts=3, pause_ms=100)

    assert len(page.wait_calls) == 3


def test_rejects_non_positive_max_attempts() -> None:
    """Failure case 2: zero/negative attempts would never scroll at all --
    a real misconfiguration, not silently a no-op."""
    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        scroll_to_load_lazy_content(_FakePage([1000]), max_attempts=0, pause_ms=700)


def test_rejects_negative_pause_ms() -> None:
    """Failure case 3: a negative pause is meaningless."""
    with pytest.raises(ValueError, match="pause_ms must be >= 0"):
        scroll_to_load_lazy_content(_FakePage([1000]), max_attempts=8, pause_ms=-1)


def test_zero_pause_ms_is_allowed() -> None:
    """A zero pause is a legitimate (if aggressive) configuration --
    not an error, unlike a negative value."""
    page = _FakePage([1000, 1000])

    scroll_to_load_lazy_content(page, max_attempts=8, pause_ms=0)

    assert page.wait_calls == [0]
