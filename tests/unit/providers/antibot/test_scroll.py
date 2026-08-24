"""Unit tests for src/providers/antibot/_scroll.py.

A fake Page tracks .evaluate()/.wait_for_timeout() calls and returns a
scripted sequence of scrollHeight values -- no real browser involved.
"""

from __future__ import annotations

import pytest

from src.providers.antibot._scroll import (
    collect_html_snapshots,
    scroll_and_collect,
    scroll_to_load_lazy_content,
)


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


# --- scroll_and_collect (docs/REQUIREMENTS.md section 9 entry 14) ------


def test_collect_fn_is_called_before_and_after_the_one_no_op_scroll() -> None:
    """Happy path: the very first (pre-scroll) page state is captured, and
    also once more after the single scroll attempt that discovers height
    never grows -- both are real content worth capturing (e.g. the first
    virtualization-window's worth of posts, seen twice since nothing
    changed), not just states reached after a *successful* scroll."""
    page = _FakePage([1000, 1000])
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(page, max_attempts=8, pause_ms=700, collect_fn=collect)

    assert calls == 2  # pre-scroll read + one post-scroll read, then stop


def test_collect_fn_is_called_after_every_scroll_step_too() -> None:
    """Happy path: a genuinely growing page gets collected after every
    successful scroll step, not just the first and last."""
    page = _FakePage([1000, 2000, 3000, 3000])
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(page, max_attempts=8, pause_ms=700, collect_fn=collect)

    assert calls == 4  # pre-scroll + 3 post-scroll reads (the last one flat, stops the loop)


def test_respects_max_attempts_even_if_height_keeps_growing_collect_variant() -> None:
    """Failure-adjacent case 1: bounded by max_attempts, same contract as
    scroll_to_load_lazy_content's identical guarantee."""
    ever_growing = [1000 * (i + 1) for i in range(20)]
    page = _FakePage(ever_growing)
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(page, max_attempts=3, pause_ms=100, collect_fn=collect)

    assert calls == 4  # pre-scroll + 3 attempts, none of which ever stabilize


def test_collect_variant_rejects_non_positive_max_attempts() -> None:
    """Failure case 2: same validation as scroll_to_load_lazy_content."""
    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        scroll_and_collect(_FakePage([1000]), max_attempts=0, pause_ms=700, collect_fn=lambda: None)


def test_collect_variant_rejects_negative_pause_ms() -> None:
    """Failure case 3: same validation as scroll_to_load_lazy_content."""
    with pytest.raises(ValueError, match="pause_ms must be >= 0"):
        scroll_and_collect(_FakePage([1000]), max_attempts=8, pause_ms=-1, collect_fn=lambda: None)


# --- collect_html_snapshots (docs/REQUIREMENTS.md section 9 entry 14 --
# the "parsed_html" half of the DOM Virtualization fix) -----------------


class _FakeContentPage(_FakePage):
    """Same scroll-height scripting as ``_FakePage``, plus a scripted
    ``.content()`` sequence -- one string per read, in the same order
    ``scroll_and_collect``'s ``collect_fn`` is invoked (pre-scroll, then
    once per successful scroll step)."""

    def __init__(self, heights: list[int], html_per_read: list[str]) -> None:
        super().__init__(heights)
        self._html = iter(html_per_read)
        self.content_calls = 0

    def content(self) -> str:
        self.content_calls += 1
        return next(self._html)


def test_captures_one_snapshot_per_read() -> None:
    """Happy path: one HTML snapshot is captured for every
    ``collect_fn`` invocation -- pre-scroll plus one per successful scroll
    step, matching ``scroll_and_collect``'s own already-tested call
    count."""
    page = _FakeContentPage(
        heights=[1000, 2000, 3000, 3000], html_per_read=["<p1/>", "<p2/>", "<p3/>", "<p4/>"]
    )

    snapshots = collect_html_snapshots(page, max_attempts=8, pause_ms=700)

    assert page.content_calls == 4
    assert snapshots == ["<p1/>", "<p2/>", "<p3/>", "<p4/>"]


def test_snapshots_are_returned_in_the_order_they_were_captured() -> None:
    """Order matters to the caller: it merges snapshots by parsing each
    in turn, and an out-of-order list would still dedupe correctly by
    post_id, but this locks in the (simpler to reason about) guarantee
    that capture order is preserved regardless."""
    page = _FakeContentPage(heights=[1000, 1000], html_per_read=["first", "second"])

    snapshots = collect_html_snapshots(page, max_attempts=8, pause_ms=700)

    assert snapshots == ["first", "second"]


def test_respects_max_attempts_for_snapshots_too() -> None:
    """Failure-adjacent case: bounded by max_attempts, same contract as
    scroll_and_collect itself -- a page whose height never stabilizes
    doesn't collect snapshots forever."""
    ever_growing_heights = [1000 * (i + 1) for i in range(20)]
    html_per_read = [f"<snap{i}/>" for i in range(20)]
    page = _FakeContentPage(heights=ever_growing_heights, html_per_read=html_per_read)

    snapshots = collect_html_snapshots(page, max_attempts=3, pause_ms=100)

    assert len(snapshots) == 4  # pre-scroll + 3 attempts, none of which ever stabilize


def test_collect_html_snapshots_rejects_non_positive_max_attempts() -> None:
    """Failure case: validation propagates unchanged from
    scroll_and_collect -- no separate check duplicated here."""
    page = _FakeContentPage(heights=[1000], html_per_read=["x"])
    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        collect_html_snapshots(page, max_attempts=0, pause_ms=700)


def test_collect_html_snapshots_rejects_negative_pause_ms() -> None:
    """Failure case: same validation propagation as above."""
    page = _FakeContentPage(heights=[1000], html_per_read=["x"])
    with pytest.raises(ValueError, match="pause_ms must be >= 0"):
        collect_html_snapshots(page, max_attempts=8, pause_ms=-1)
