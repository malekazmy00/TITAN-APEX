"""Unit tests for src/providers/antibot/_scroll.py.

A fake Page tracks .evaluate()/.wait_for_timeout() calls and returns a
scripted sequence of scrollHeight values -- no real browser involved.
"""

from __future__ import annotations

import pytest

from src.providers.antibot._scroll import (
    RequestCounter,
    collect_html_snapshots,
    poll_until_idle,
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
#
# Revised after a real, CI-confirmed failure: the original version
# copied scroll_to_load_lazy_content's height-growth-based early stop,
# which is invalid for a virtualized target (rendered height never
# grows once eviction kicks in, even though genuinely new content keeps
# loading) -- these tests now lock in the "always run exactly
# max_attempts cycles, no early exit" contract instead, plus the new
# synthetic scroll Event dispatch.


def test_collect_fn_is_called_once_before_any_scroll_and_once_per_attempt() -> None:
    """Happy path: the very first (pre-scroll) page state is captured,
    plus exactly one more per scroll attempt -- no early exit, since
    scrollHeight is no longer read or compared at all."""
    page = _FakePage([1000, 1000])  # heights are irrelevant now, never read
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(page, max_attempts=3, pause_ms=700, collect_fn=collect)

    assert calls == 4  # pre-scroll + 3 attempts, unconditionally


def test_every_scroll_step_dispatches_a_synthetic_scroll_event() -> None:
    """The real, separately-discovered fix this revision adds: a plain
    ``scrollTo()`` alone doesn't reliably fire a 'scroll' event once a
    virtualized list's rendered content is too short to need scrolling
    -- so every attempt must also explicitly dispatch one, which
    ``templates/feed.html``'s own loadMore() trigger listens for
    regardless of whether the event is browser- or script-generated."""
    page = _FakePage([1000, 1000, 1000])

    scroll_and_collect(page, max_attempts=2, pause_ms=700, collect_fn=lambda: None)

    assert len(page.evaluate_calls) == 2
    for script in page.evaluate_calls:
        assert "dispatchEvent(new Event('scroll'))" in script
        assert "scrollTo(0, document.body.scrollHeight)" in script


def test_respects_max_attempts_as_the_only_stopping_condition() -> None:
    """Failure-adjacent case 1: bounded strictly by max_attempts -- the
    same guarantee as scroll_to_load_lazy_content, just unconditional
    here instead of an upper bound on top of an early-exit heuristic."""
    page = _FakePage([1000] * 25)
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(page, max_attempts=8, pause_ms=100, collect_fn=collect)

    assert calls == 9  # pre-scroll + all 8 attempts, every time


def test_collect_variant_rejects_non_positive_max_attempts() -> None:
    """Failure case 2: same validation as scroll_to_load_lazy_content."""
    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        scroll_and_collect(_FakePage([1000]), max_attempts=0, pause_ms=700, collect_fn=lambda: None)


def test_collect_variant_rejects_negative_pause_ms() -> None:
    """Failure case 3: same validation as scroll_to_load_lazy_content."""
    with pytest.raises(ValueError, match="pause_ms must be >= 0"):
        scroll_and_collect(_FakePage([1000]), max_attempts=8, pause_ms=-1, collect_fn=lambda: None)


# --- settle_fn (docs/REQUIREMENTS.md section 9's "DOM Virtualization
# Instability" investigation -- the real, CI-confirmed race the
# "more generous constants" fix above only narrowed, never closed) -----


def test_settle_fn_defaults_to_none_and_is_never_required() -> None:
    """Happy path (backward compatibility): every caller written before
    this revision -- including every other test in this file -- never
    passes settle_fn, and must keep behaving exactly as before."""
    page = _FakePage([1000, 1000, 1000])

    scroll_and_collect(page, max_attempts=2, pause_ms=700, collect_fn=lambda: None)

    assert page.wait_calls == [700, 700]


def test_settle_fn_runs_once_per_scroll_attempt_before_the_pause() -> None:
    """The actual fix: a real completion signal (settle_fn) is given a
    chance to run *before* the fixed pause_ms sleep on every attempt --
    not just once, not after the pause (which would defeat the point:
    the pause would still race the same fetch settle_fn is meant to wait
    for)."""
    page = _FakePage([1000, 1000, 1000])
    order: list[str] = []

    def settle() -> None:
        order.append("settle")

    def collect() -> None:
        order.append("collect")

    scroll_and_collect(page, max_attempts=2, pause_ms=700, collect_fn=collect, settle_fn=settle)

    # pre-scroll collect, then (settle, collect) per attempt -- settle_fn
    # is not called before the very first, pre-scroll read (there's no
    # scroll step to settle yet).
    assert order == ["collect", "settle", "collect", "settle", "collect"]


def test_settle_fn_is_not_called_when_max_attempts_validation_fails() -> None:
    """Failure-adjacent case: validation happens before any scroll step
    (and thus before settle_fn could ever run) -- same ordering
    guarantee scroll_to_load_lazy_content's own validation already has."""
    calls = 0

    def settle() -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        scroll_and_collect(
            _FakePage([1000]), max_attempts=0, pause_ms=700, collect_fn=lambda: None,
            settle_fn=settle,
        )

    assert calls == 0


def test_collect_html_snapshots_passes_settle_fn_through() -> None:
    """collect_html_snapshots is a thin wrapper -- confirms settle_fn
    reaches scroll_and_collect unchanged through it too, not just when
    calling scroll_and_collect directly."""
    page = _FakeContentPage(heights=[1000, 1000], html_per_read=["first", "second"])
    calls = 0

    def settle() -> None:
        nonlocal calls
        calls += 1

    snapshots = collect_html_snapshots(page, max_attempts=1, pause_ms=700, settle_fn=settle)

    assert snapshots == ["first", "second"]
    assert calls == 1


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
    ``collect_fn`` invocation -- pre-scroll plus one per scroll attempt,
    unconditionally (see this module's own "Revision" docstring for why
    there's no early exit here), matching ``scroll_and_collect``'s own
    already-tested call count."""
    page = _FakeContentPage(
        heights=[1000, 1000, 1000, 1000], html_per_read=["<p1/>", "<p2/>", "<p3/>", "<p4/>"]
    )

    snapshots = collect_html_snapshots(page, max_attempts=3, pause_ms=700)

    assert page.content_calls == 4
    assert snapshots == ["<p1/>", "<p2/>", "<p3/>", "<p4/>"]


def test_snapshots_are_returned_in_the_order_they_were_captured() -> None:
    """Order matters to the caller: it merges snapshots by parsing each
    in turn, and an out-of-order list would still dedupe correctly by
    post_id, but this locks in the (simpler to reason about) guarantee
    that capture order is preserved regardless."""
    page = _FakeContentPage(heights=[1000, 1000], html_per_read=["first", "second"])

    snapshots = collect_html_snapshots(page, max_attempts=1, pause_ms=700)

    assert snapshots == ["first", "second"]


def test_respects_max_attempts_for_snapshots_too() -> None:
    """Failure-adjacent case: bounded strictly by max_attempts, same
    contract as scroll_and_collect itself."""
    html_per_read = [f"<snap{i}/>" for i in range(9)]
    page = _FakeContentPage(heights=[1000] * 9, html_per_read=html_per_read)

    snapshots = collect_html_snapshots(page, max_attempts=8, pause_ms=100)

    assert len(snapshots) == 9  # pre-scroll + all 8 attempts, every time


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


# --- poll_until_idle (docs/REQUIREMENTS.md section 9's "DOM
# Virtualization Instability" investigation, "Third revision" --
# the corrected settle_fn building block, after a real CI run proved
# the first implementation, page.wait_for_load_state("networkidle"),
# was a no-op for repeated same-page fetches) --------------------------


class _FakeClock:
    """Deterministic clock for poll_until_idle -- makes timeout/quiet-
    window edges exact without any real sleeping. Each sleep_fn(ms) call
    advances the clock by ms milliseconds; now_fn reads it back."""

    def __init__(self) -> None:
        self.seconds = 0.0
        self.sleep_calls: list[int] = []

    def now(self) -> float:
        return self.seconds

    def sleep(self, ms: int) -> None:
        self.sleep_calls.append(ms)
        self.seconds += ms / 1000


def test_poll_until_idle_returns_true_once_idle_for_the_full_quiet_window() -> None:
    """Happy path: is_idle_fn true from the very first check -- settles
    (returns True) once quiet_ms worth of idle time has accumulated,
    nowhere near timeout_ms."""
    clock = _FakeClock()

    settled = poll_until_idle(
        lambda: True, clock.sleep, timeout_ms=5_000, quiet_ms=200, now_fn=clock.now
    )

    assert settled is True
    assert len(clock.sleep_calls) == 4  # 50ms polls: 0, .05, .10, .15 -> settles at .20
    assert clock.seconds == pytest.approx(0.20)


def test_poll_until_idle_returns_false_when_never_idle_before_timeout() -> None:
    """Failure-adjacent case 1: is_idle_fn always false -- times out
    (returns False) at timeout_ms, never settles."""
    clock = _FakeClock()

    settled = poll_until_idle(
        lambda: False, clock.sleep, timeout_ms=200, quiet_ms=500, now_fn=clock.now
    )

    assert settled is False
    assert clock.seconds == pytest.approx(0.20)


def test_poll_until_idle_resets_the_quiet_window_on_renewed_activity() -> None:
    """The actual point of this function, and the exact behavior the
    real request-tracking settle_fn relies on: a quiet period that gets
    interrupted by renewed activity (e.g. one more still-in-flight
    fetch) must start counting over from scratch, not credit time
    accumulated before the interruption."""
    # idle, idle, BUSY (resets the window), idle forever after.
    states = iter([True, True, False, *([True] * 20)])

    def is_idle() -> bool:
        return next(states)

    clock = _FakeClock()

    settled = poll_until_idle(
        is_idle, clock.sleep, timeout_ms=5_000, quiet_ms=200, now_fn=clock.now
    )

    assert settled is True
    # Without the reset this would settle after 4 polls (see the happy
    # path test above) -- the interruption forces it to restart the
    # quiet window from the first idle check after it, so strictly more
    # polls happen. (Not an exact count: float accumulation in repeated
    # 50ms adds can tip a boundary check either way by one poll -- the
    # real, meaningful guarantee is "more than the no-reset baseline",
    # not a bit-exact tally.)
    assert len(clock.sleep_calls) > 4


def test_poll_until_idle_rejects_non_positive_timeout_ms() -> None:
    """Failure case 2: a zero/negative timeout would never poll at all --
    a real misconfiguration, not silently a no-op."""
    with pytest.raises(ValueError, match="timeout_ms must be > 0"):
        poll_until_idle(lambda: True, lambda _ms: None, timeout_ms=0)


def test_poll_until_idle_rejects_negative_quiet_ms() -> None:
    """Failure case 3: a negative quiet window is meaningless."""
    with pytest.raises(ValueError, match="quiet_ms must be >= 0"):
        poll_until_idle(lambda: True, lambda _ms: None, timeout_ms=1_000, quiet_ms=-1)


# --- RequestCounter (the is_idle_fn each provider hands
# poll_until_idle, backed by live page.on() listener state) -----------


def test_request_counter_starts_idle() -> None:
    """Happy path: with no requests ever seen, it's idle from the start
    -- the common case (the very first settle_fn call of a crawl)."""
    counter = RequestCounter()

    assert counter.is_idle() is True


def test_request_counter_is_not_idle_while_a_request_is_outstanding() -> None:
    """Happy path: one on_start() with no matching on_settle() yet means
    something is genuinely still in flight."""
    counter = RequestCounter()

    counter.on_start()

    assert counter.is_idle() is False


def test_request_counter_is_idle_again_once_every_request_settles() -> None:
    """Happy path: on_settle() (success or failure -- both wired to it,
    see camoufox_provider.py/patchright_provider.py) balances a matching
    on_start(), including more than one concurrent request."""
    counter = RequestCounter()

    counter.on_start()
    counter.on_start()
    counter.on_settle()
    assert counter.is_idle() is False  # one still outstanding
    counter.on_settle()

    assert counter.is_idle() is True


def test_request_counter_never_goes_negative_on_an_unmatched_settle() -> None:
    """Failure-adjacent case: a settle event for a request this counter
    never saw start (e.g. one already in flight before listeners were
    attached) must not push the tally negative -- that would make
    is_idle() wrongly report idle while something else might still
    genuinely be outstanding on the very next real on_start()/on_settle()
    pair (a negative tally would need an extra, spurious on_settle() to
    cancel out before is_idle() ever reported False again correctly)."""
    counter = RequestCounter()

    counter.on_settle()

    assert counter.is_idle() is True
    counter.on_start()
    assert counter.is_idle() is False


def test_request_counter_on_start_and_on_settle_accept_an_ignored_argument() -> None:
    """Both are wired directly as page.on(event, handler) listeners,
    which always pass the triggering Request object positionally -- must
    accept (and ignore) it, not just work when called with none."""
    counter = RequestCounter()

    counter.on_start("a fake Request object")
    counter.on_settle("the same shape")

    assert counter.is_idle() is True
