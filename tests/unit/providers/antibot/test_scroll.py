"""Unit tests for src/providers/antibot/_scroll.py.

A fake Page tracks .evaluate()/.wait_for_timeout() calls and returns a
scripted sequence of scrollHeight values -- no real browser involved.
"""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from src.providers.antibot._scroll import (
    RequestCounter,
    collect_html_snapshots,
    poll_until_idle,
    randomized_pause_ms,
    randomized_scroll_delta,
    scroll_and_collect,
    scroll_to_load_lazy_content,
)


class _FakeMouse:
    """Stands in for a real Playwright/Patchright ``Page.mouse`` --
    :func:`scroll_and_collect` only ever calls ``.move()`` (once) and
    ``.wheel(delta_x, delta_y)`` (per attempt) on it (docs/REQUIREMENTS.md
    section 9 entry 17's "Fourth revision"), never anything else."""

    def __init__(self) -> None:
        self.wheel_calls: list[tuple[float, float]] = []
        self.move_calls: list[tuple[float, float]] = []

    def move(self, x: float, y: float) -> None:
        self.move_calls.append((x, y))

    def wheel(self, delta_x: float, delta_y: float) -> None:
        self.wheel_calls.append((delta_x, delta_y))


class _FakePage:
    def __init__(self, heights: list[int]) -> None:
        # heights[0] is the initial height (read once before the loop);
        # heights[1:] are what each subsequent "after scroll" read returns.
        self._heights = iter(heights)
        self.evaluate_calls: list[str] = []
        self.wait_calls: list[int] = []
        self.mouse = _FakeMouse()

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


# --- randomized_scroll_delta / randomized_pause_ms (docs/REQUIREMENTS.md
# section 9 entry 17's "Fourth revision" -- the real, CI-confirmed fix
# for the double-dispatch race: page.mouse.wheel() + randomized
# delta/pause instead of the old scrollTo()+synthetic dispatchEvent
# pair) --------------------------------------------------------------


def test_randomized_scroll_delta_stays_within_the_given_range() -> None:
    """Happy path: every draw lands inside [low, high], never outside
    it either direction."""
    rng = random.Random(0)

    for _ in range(200):
        delta = randomized_scroll_delta(rng, delta_range=(100.0, 200.0))
        assert 100.0 <= delta <= 200.0


def test_randomized_scroll_delta_varies_across_calls() -> None:
    """The actual point of randomizing it at all: repeated calls (same
    rng, same range) don't all return the same value -- unlike the old
    fixed scrollTo(0, document.body.scrollHeight) jump."""
    rng = random.Random(0)

    deltas = [randomized_scroll_delta(rng) for _ in range(10)]

    assert len(set(deltas)) > 1


def test_randomized_scroll_delta_rejects_a_negative_low_end() -> None:
    """Failure-adjacent case 1: a negative delta would scroll upward,
    not a meaningful configuration for this function's own contract."""
    with pytest.raises(ValueError, match="delta_range's low end must be >= 0"):
        randomized_scroll_delta(random.Random(0), delta_range=(-1.0, 100.0))


def test_randomized_scroll_delta_rejects_a_high_end_below_the_low_end() -> None:
    """Failure-adjacent case 2: an inverted range is meaningless."""
    with pytest.raises(ValueError, match="high end .* must be >= its low end"):
        randomized_scroll_delta(random.Random(0), delta_range=(200.0, 100.0))


def test_randomized_pause_ms_drifts_upward_across_a_session() -> None:
    """The actual "fatigue"/attention-span model: holding jitter fixed
    (a rng that always draws the same jitter multiplier every time --
    the midpoint of the default range), the *mean* pause at the last
    step of a session is measurably longer than at the first, not flat
    the way a plain per-step-independent jitter alone would be."""

    class _FixedJitterRng:
        """A minimal stand-in exposing only the .uniform() rng.uniform
        calls this function actually makes -- always returns the
        midpoint of whatever range it's asked for, so only the fatigue
        drift (not jitter noise) affects the result."""

        def uniform(self, low: float, high: float) -> float:
            return (low + high) / 2

    rng = _FixedJitterRng()

    first_step = randomized_pause_ms(1000, step_index=0, total_steps=10, rng=rng)  # type: ignore[arg-type]
    last_step = randomized_pause_ms(1000, step_index=9, total_steps=10, rng=rng)  # type: ignore[arg-type]

    assert last_step > first_step


def test_randomized_pause_ms_skips_fatigue_drift_for_a_single_step_session() -> None:
    """Failure-adjacent case: total_steps=1 has no "progress through the
    session" to model -- the mean pause is just base_pause_ms, jitter
    still applied on top (not a crash from a division by zero in the
    progress calculation)."""
    rng = random.Random(0)

    result = randomized_pause_ms(1000, step_index=0, total_steps=1, rng=rng)

    assert isinstance(result, int)
    assert result >= 0


def test_randomized_pause_ms_never_returns_a_negative_value() -> None:
    """Happy path: even at the low end of the jitter range, the result
    is clamped at zero, never negative (a negative wait is meaningless
    to page.wait_for_timeout())."""
    rng = random.Random(0)

    for step in range(5):
        result = randomized_pause_ms(0, step_index=step, total_steps=5, rng=rng)
        assert result >= 0


def test_randomized_pause_ms_rejects_a_negative_base_pause() -> None:
    """Failure-adjacent case 1: a negative base pause is meaningless."""
    with pytest.raises(ValueError, match="base_pause_ms must be >= 0"):
        randomized_pause_ms(-1, step_index=0, total_steps=5, rng=random.Random(0))


def test_randomized_pause_ms_rejects_non_positive_total_steps() -> None:
    """Failure-adjacent case 2: zero/negative total_steps is meaningless
    -- there's no session to model at all."""
    with pytest.raises(ValueError, match="total_steps must be > 0"):
        randomized_pause_ms(1000, step_index=0, total_steps=0, rng=random.Random(0))


def test_randomized_pause_ms_rejects_a_step_index_outside_the_session() -> None:
    """Failure case 3: a step_index at or beyond total_steps (or
    negative) can't be a real position within the session."""
    with pytest.raises(ValueError, match=r"step_index \(5\) must be in \[0, 5\)"):
        randomized_pause_ms(1000, step_index=5, total_steps=5, rng=random.Random(0))


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


def test_every_scroll_step_uses_a_real_mouse_wheel_not_a_synthetic_dispatch() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Fourth revision" --
    the actual, CI-confirmed fix for the double-dispatch race: no more
    ``page.evaluate()``/synthetic ``dispatchEvent('scroll')`` at all --
    every attempt drives a real ``page.mouse.wheel()`` input instead, no
    exceptions."""
    page = _FakePage([1000, 1000, 1000])

    scroll_and_collect(
        page, max_attempts=2, pause_ms=700, collect_fn=lambda: None, rng=random.Random(0)
    )

    assert page.evaluate_calls == []  # no scrollTo()/dispatchEvent() left at all
    assert len(page.mouse.wheel_calls) == 2
    for delta_x, delta_y in page.mouse.wheel_calls:
        assert delta_x == 0
        assert delta_y > 0  # a real downward scroll every time


def test_moves_the_mouse_into_the_viewport_once_before_any_wheel() -> None:
    """A real, empirically-confirmed requirement (this module's own
    docstring's "Fourth revision"): page.mouse.wheel() alone produced
    zero real scroll in a headless Camoufox session without a prior
    page.mouse.move() -- confirmed by hand, not assumed. Moved once,
    before the first wheel -- not re-moved every attempt, since the
    cursor stays wherever it's put."""
    page = _FakePage([1000, 1000, 1000])

    scroll_and_collect(
        page, max_attempts=3, pause_ms=700, collect_fn=lambda: None, rng=random.Random(0)
    )

    assert len(page.mouse.move_calls) == 1
    assert len(page.mouse.wheel_calls) == 3


def test_hover_fn_runs_before_every_attempt_not_once() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision"
    (replacing the "Seventh revision"'s container_selector, a real,
    user-requested follow-up to the "Fourth revision" fixed-move above):
    when hover_fn is given, it replaces the fixed page.mouse.move(200,
    200) entirely -- called once per attempt (recomputing the
    container's own real position every time, in whatever way the
    caller's own hover_fn does that), never the fixed-coordinate
    one-time move at all."""
    page = _FakePage([1000, 1000, 1000, 1000])
    hover_calls = 0

    def hover_fn() -> bool:
        nonlocal hover_calls
        hover_calls += 1
        return True

    scroll_and_collect(
        page,
        max_attempts=4,
        pause_ms=700,
        collect_fn=lambda: None,
        rng=random.Random(0),
        hover_fn=hover_fn,
    )

    assert hover_calls == 4  # once per attempt, not once total
    assert page.mouse.move_calls == []  # the fixed-coordinate fallback never runs
    assert len(page.mouse.wheel_calls) == 4


def test_hover_fn_none_keeps_the_fixed_move_fallback() -> None:
    """The other half of the same revision: hover_fn's default (None)
    must reproduce the exact prior behavior for every caller that hasn't
    been updated to supply one -- same guarantee this module has kept
    at every prior revision."""
    page = _FakePage([1000, 1000])

    scroll_and_collect(
        page, max_attempts=1, pause_ms=700, collect_fn=lambda: None, hover_fn=None
    )

    assert page.mouse.move_calls == [(200, 200)]


def test_hover_fn_returning_false_skips_the_trigger_but_still_collects_and_stops() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision": a
    hover_fn that fails to position the cursor even after its own
    best-effort recovery (e.g. dismissing a known overlay and retrying)
    returns False -- the same stop-early contract trigger_and_wait_fn
    already has. This attempt's own wheel trigger is skipped entirely
    (nothing meaningful to scroll toward), but its pause+collect_fn()
    still runs (the same "Sixth revision" promise), and the loop stops
    -- no further attempts."""
    page = _FakePage([1000, 1000, 1000])
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    scroll_and_collect(
        page,
        max_attempts=5,
        pause_ms=700,
        collect_fn=collect,
        rng=random.Random(0),
        hover_fn=lambda: False,
    )

    assert calls == 2  # pre-scroll collect + this step's own -- not skipped
    assert page.mouse.wheel_calls == []  # the trigger never ran at all -- nothing to scroll to
    assert len(page.wait_calls) == 1  # this step's own (randomized) pause still ran too


def test_scroll_deltas_are_randomized_per_step_not_a_fixed_jump() -> None:
    """The actual point of switching to page.mouse.wheel(): each step
    gets its own randomized delta (docs/REQUIREMENTS.md section 9 entry
    17), not the old identical scrollTo(0, document.body.scrollHeight)
    jump repeated every time -- confirmed here by reproducing the exact
    sequence via the same seeded rng and the pure function under test,
    not by asserting mere inequality (which a flaky one-in-a-billion
    coincidence could pass)."""
    rng_for_page = random.Random(1234)
    page = _FakePage([1000] * 10)

    scroll_and_collect(
        page, max_attempts=3, pause_ms=700, collect_fn=lambda: None, rng=rng_for_page
    )

    # Replays scroll_and_collect's own exact per-step draw order (delta,
    # then randomized_pause_ms's own draw) against an identically-seeded
    # rng -- not just the delta calls in isolation, which would desync
    # from the real sequence the moment a pause draw happens in between.
    rng_for_expected = random.Random(1234)
    expected_deltas = []
    for step in range(3):
        expected_deltas.append(randomized_scroll_delta(rng_for_expected))
        randomized_pause_ms(700, step, 3, rng_for_expected)
    assert [delta_y for _, delta_y in page.mouse.wheel_calls] == expected_deltas
    assert len(set(expected_deltas)) == 3  # genuinely different every step


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


# --- trigger_and_wait_fn (docs/REQUIREMENTS.md section 9 entry 17's
# "Fifth revision" -- replaces settle_fn entirely: a callable given the
# actual scroll trigger itself, so a caller can arm a real completion
# listener *before* running it, e.g. page.expect_response(), instead of
# settle_fn's old "wait after the fact" ordering, which was itself a
# real, CI-confirmed race once a response is fast enough) --------------


def test_trigger_and_wait_fn_defaults_to_none_and_is_never_required() -> None:
    """Happy path (backward compatibility): every caller written before
    trigger_and_wait_fn existed -- including every other test in this
    file -- never passes it, and must keep working with no separate
    wait step at all: the trigger just runs directly. The pause itself
    is randomized per step (docs/REQUIREMENTS.md section 9 entry 17's
    "Fourth revision" -- no longer a fixed ``pause_ms`` repeated
    identically), so this locks in the exact randomized_pause_ms
    sequence for a fixed seed instead of a bare constant."""
    page = _FakePage([1000, 1000, 1000])

    scroll_and_collect(
        page, max_attempts=2, pause_ms=700, collect_fn=lambda: None, rng=random.Random(99)
    )

    rng_for_expected = random.Random(99)
    expected_waits = []
    for step in range(2):
        randomized_scroll_delta(rng_for_expected)
        expected_waits.append(randomized_pause_ms(700, step, 2, rng_for_expected))
    assert page.wait_calls == expected_waits


def test_trigger_and_wait_fn_receives_the_real_trigger_and_runs_it() -> None:
    """The actual point of this parameter: it is handed the real scroll
    trigger (not called automatically) -- a caller that never invokes
    it gets no scroll at all, and one that does invoke it drives the
    exact same real page.mouse.wheel() this module always uses."""
    page = _FakePage([1000, 1000, 1000])
    order: list[str] = []

    def trigger_and_wait(trigger: Callable[[], None]) -> bool:
        order.append("wait_armed")
        trigger()
        order.append("wait_resolved")
        return True

    def collect() -> None:
        order.append("collect")

    scroll_and_collect(
        page, max_attempts=2, pause_ms=700, collect_fn=collect,
        trigger_and_wait_fn=trigger_and_wait,
    )

    # pre-scroll collect, then (wait_armed, wait_resolved, collect) per
    # attempt -- trigger_and_wait_fn is not called before the very
    # first, pre-scroll read (there's no scroll step to wait on yet).
    assert order == [
        "collect", "wait_armed", "wait_resolved", "collect",
        "wait_armed", "wait_resolved", "collect",
    ]
    assert len(page.mouse.wheel_calls) == 2


def test_trigger_and_wait_fn_returning_false_stops_the_loop_after_its_own_collect() -> None:
    """The actual new behavior this revision adds: a real "no more
    pages" signal (e.g. a page.expect_response() timeout, or a real
    page_info.has_next_page: false) stops scroll_and_collect after the
    *current* step, instead of burning through the remaining
    max_attempts on guaranteed no-ops.

    docs/REQUIREMENTS.md section 9 entry 17's "Sixth revision" -- a
    real, confirmed bug this test used to lock in by mistake: the
    current step's own pause+collect must still run even when
    trigger_and_wait_fn returns False, since the response finally
    reporting "no more pages" is often the same one whose own new
    content this exact step's trigger just caused to load. Only the
    *next* step is skipped."""
    page = _FakePage([1000] * 10)
    calls = 0

    def collect() -> None:
        nonlocal calls
        calls += 1

    def trigger_and_wait(trigger: Callable[[], None]) -> bool:
        trigger()
        return False  # every attempt reports "nothing more to load"

    scroll_and_collect(
        page, max_attempts=5, pause_ms=700, collect_fn=collect,
        trigger_and_wait_fn=trigger_and_wait, rng=random.Random(7),
    )

    rng_for_expected = random.Random(7)
    randomized_scroll_delta(rng_for_expected)
    expected_wait = randomized_pause_ms(700, 0, 5, rng_for_expected)

    assert calls == 2  # pre-scroll collect + this step's own -- not skipped
    assert len(page.mouse.wheel_calls) == 1  # the trigger still ran once, before stopping
    assert page.wait_calls == [expected_wait]  # this step's own (randomized) pause still ran too


def test_trigger_and_wait_fn_is_not_called_when_max_attempts_validation_fails() -> None:
    """Failure-adjacent case: validation happens before any scroll step
    (and thus before trigger_and_wait_fn could ever run) -- same
    ordering guarantee scroll_to_load_lazy_content's own validation
    already has."""
    calls = 0

    def trigger_and_wait(trigger: Callable[[], None]) -> bool:
        nonlocal calls
        calls += 1
        trigger()
        return True

    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        scroll_and_collect(
            _FakePage([1000]), max_attempts=0, pause_ms=700, collect_fn=lambda: None,
            trigger_and_wait_fn=trigger_and_wait,
        )

    assert calls == 0


def test_collect_html_snapshots_passes_trigger_and_wait_fn_through() -> None:
    """collect_html_snapshots is a thin wrapper -- confirms
    trigger_and_wait_fn reaches scroll_and_collect unchanged through it
    too, not just when calling scroll_and_collect directly."""
    page = _FakeContentPage(heights=[1000, 1000], html_per_read=["first", "second"])
    calls = 0

    def trigger_and_wait(trigger: Callable[[], None]) -> bool:
        nonlocal calls
        calls += 1
        trigger()
        return True

    snapshots = collect_html_snapshots(
        page, max_attempts=1, pause_ms=700, trigger_and_wait_fn=trigger_and_wait
    )

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


def test_collect_html_snapshots_passes_hover_fn_through() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision":
    collect_html_snapshots is a thin wrapper -- confirms hover_fn reaches
    scroll_and_collect unchanged through it too, not just when calling
    scroll_and_collect directly."""
    page = _FakeContentPage(heights=[1000, 1000], html_per_read=["first", "second"])
    hover_calls = 0

    def hover_fn() -> bool:
        nonlocal hover_calls
        hover_calls += 1
        return True

    collect_html_snapshots(page, max_attempts=1, pause_ms=700, hover_fn=hover_fn)

    assert hover_calls == 1
    assert page.mouse.move_calls == []


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
