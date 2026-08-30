"""Unit tests for src/providers/antibot/camoufox_provider.py.

The browser-driving call is always injected: these tests never launch a
real Camoufox browser or touch the network.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AntibotError, BrowserCrashedError
from src.core.interfaces.antibot_provider import LiveDomSelectors, LoginFlow
from src.providers.antibot.camoufox_provider import (
    CamoufoxProvider,
    _classify_solve_exception,
    _RawSolve,
)


def test_solve_returns_a_populated_solution() -> None:
    """Happy path: a successful browser-driving call yields a full Solution."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        assert url == "https://example.com/"
        assert timeout_ms == 30_000
        assert post_load_wait_ms == 5_000
        return _RawSolve(
            url="https://example.com/",
            html="<html><body>solved</body></html>",
            status=200,
            cookies={"session": "abc123"},
        )

    provider = CamoufoxProvider(solve_fn=fake_solve)

    solution = provider.solve("https://example.com/")

    assert solution.url == "https://example.com/"
    assert solution.html == "<html><body>solved</body></html>"
    assert solution.status_code == 200
    assert solution.cookies == {"session": "abc123"}
    assert solution.solved_at is not None


def test_post_load_wait_ms_reaches_the_solve_function() -> None:
    """The whole point of this provider: a configurable extra wait after
    load actually reaches the browser-driving call."""
    seen: dict[str, int] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        seen["post_load_wait_ms"] = post_load_wait_ms
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(post_load_wait_ms=8_000, solve_fn=fake_solve)
    provider.solve("https://example.com/")

    assert seen["post_load_wait_ms"] == 8_000


def test_non_positive_timeout_raises_antibot_error() -> None:
    """Failure case 1: a non-positive timeout is meaningless."""
    with pytest.raises(AntibotError, match="timeout_ms must be > 0"):
        CamoufoxProvider(timeout_ms=0)


def test_negative_post_load_wait_ms_raises_antibot_error() -> None:
    """Failure case 2: a negative wait is meaningless."""
    with pytest.raises(AntibotError, match="post_load_wait_ms must be >= 0"):
        CamoufoxProvider(post_load_wait_ms=-1)


def test_solve_function_failure_propagates_as_antibot_error() -> None:
    """Failure case 3: the browser-driving call failing (browser launch,
    navigation, ...) surfaces as AntibotError, not a raw/unexpected type --
    _default_camoufox_solve itself is what wraps the real Camoufox/Playwright
    exceptions; this confirms CamoufoxProvider.solve() doesn't swallow or
    mistranslate whatever AntibotError it's given."""

    def failing_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        raise AntibotError(f"camoufox failed to solve {url}: browser launch failed")

    provider = CamoufoxProvider(solve_fn=failing_solve)

    with pytest.raises(AntibotError, match="browser launch failed"):
        provider.solve("https://example.com/")


def test_classify_solve_exception_returns_browser_crashed_error_when_a_real_crash_fired() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17, answering a user review's
    direct question: does the classification logic itself (not just the
    retry loop around an already-classified fake exception) actually
    convert a real page.on("crash")/browser.on(...) firing into
    BrowserCrashedError? This is the exact decision
    _default_camoufox_solve's own (untestable-without-a-real-browser,
    `# pragma: no cover`) except-block makes -- pulled out as a pure
    function specifically so this is checkable directly, without faking
    an entire browser session."""
    result = _classify_solve_exception(
        browser_crashed=True, url="https://example.com/", exc=Exception("Target closed")
    )

    assert isinstance(result, BrowserCrashedError)
    assert "browser engine crashed" in str(result)


def test_classify_solve_exception_returns_plain_antibot_error_when_no_crash_fired() -> None:
    """The other half: a PlaywrightError that isn't a real engine crash
    (a denied request, a legitimate timeout -- browser_crashed stays
    False) must classify as plain AntibotError, not BrowserCrashedError
    -- CamoufoxProvider.solve()'s own retry loop must not retry these."""
    result = _classify_solve_exception(
        browser_crashed=False, url="https://example.com/", exc=Exception("denied")
    )

    assert type(result) is AntibotError
    assert not isinstance(result, BrowserCrashedError)


def test_browser_crash_retries_on_a_fresh_call_and_eventually_succeeds() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17: a real, kernel-log-
    confirmed Firefox engine crash (BrowserCrashedError specifically, not
    any other AntibotError) is retried -- solve_fn is called again (a
    fresh browser instance in the real, non-injected path, since
    _default_camoufox_solve's own `with Camoufox(...)` always creates a
    new one), not given up on after the first failure."""
    calls = 0

    def flaky_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise BrowserCrashedError(f"camoufox's browser engine crashed mid-solve for {url}")
        return _RawSolve(url=url, html="<html>solved</html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=flaky_solve, max_browser_crash_attempts=3)

    solution = provider.solve("https://example.com/")

    assert calls == 3  # two crashes, then a fresh third attempt succeeded
    assert solution.html == "<html>solved</html>"


def test_browser_crash_exhausts_max_attempts_and_raises() -> None:
    """Bounded, not open-ended: a browser that keeps crashing every
    single attempt still gives up after max_browser_crash_attempts, not
    forever."""
    calls = 0

    def always_crashes(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        raise BrowserCrashedError(f"camoufox's browser engine crashed mid-solve for {url}")

    provider = CamoufoxProvider(solve_fn=always_crashes, max_browser_crash_attempts=3)

    with pytest.raises(BrowserCrashedError):
        provider.solve("https://example.com/")

    assert calls == 3  # exactly the configured bound, not more


def test_non_crash_antibot_error_is_not_retried() -> None:
    """A real, reproducible solve failure that *isn't* a browser crash
    (a denied request, no items found, ...) must not be retried the same
    way -- retrying it would either waste time or mask a real problem
    behind an eventual lucky pass."""
    calls = 0

    def denied_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        raise AntibotError(f"camoufox failed to solve {url}: denied")

    provider = CamoufoxProvider(solve_fn=denied_solve, max_browser_crash_attempts=3)

    with pytest.raises(AntibotError, match="denied"):
        provider.solve("https://example.com/")

    assert calls == 1  # not retried at all


def test_non_positive_max_browser_crash_attempts_raises_antibot_error() -> None:
    """Failure case: a non-positive retry bound is meaningless."""
    with pytest.raises(AntibotError, match="max_browser_crash_attempts must be > 0"):
        CamoufoxProvider(max_browser_crash_attempts=0)


def test_click_selector_reaches_the_solve_function() -> None:
    """The cookie-consent-wall round's whole point for this provider:
    click_selector actually reaches the browser-driving call (unlike
    ByparrProvider, which can only log that it's unsupported)."""
    seen: dict[str, str | None] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        seen["click_selector"] = click_selector
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=fake_solve)
    provider.solve("https://example.com/", click_selector="#accept-cookies")

    assert seen["click_selector"] == "#accept-cookies"


def test_click_selector_defaults_to_none_when_not_given() -> None:
    """Backward compatible: solve(url) alone (no click_selector) must still
    work exactly as before this round -- the whole existing test suite
    above already exercises this, this just makes the default explicit."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        assert click_selector is None
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200


def test_zero_post_load_wait_ms_is_allowed() -> None:
    """post_load_wait_ms=0 is a legitimate (if pointless) configuration --
    not an error, unlike a negative value."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(post_load_wait_ms=0, solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200


def test_extraction_selectors_reaches_the_solve_function() -> None:
    """docs/REQUIREMENTS.md section 9 entry 12's whole point for this
    provider: extraction_selectors actually reaches the browser-driving
    call, unlike ByparrProvider (which can only log that it's unsupported,
    no live page to query)."""
    seen: dict[str, LiveDomSelectors | None] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        seen["extraction_selectors"] = extraction_selectors
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=fake_solve)
    selectors = LiveDomSelectors(item='[data-role="post"]', fields={"author": "::text"})
    provider.solve("https://example.com/", extraction_selectors=selectors)

    assert seen["extraction_selectors"] == selectors


def test_extraction_selectors_defaults_to_none_when_not_given() -> None:
    """Backward compatible: solve(url) alone must still work exactly as
    before this round."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        assert extraction_selectors is None
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200


def test_solve_fn_items_reach_the_returned_solution() -> None:
    """The whole point of extraction_selectors: items the browser-driving
    call extracted live must reach Solution.items unchanged, not be
    dropped or re-derived from html."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        return _RawSolve(
            url=url,
            html="<html></html>",
            status=200,
            cookies={},
            items=[{"author": "alice"}, {"author": "bob"}],
        )

    provider = CamoufoxProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.items == [{"author": "alice"}, {"author": "bob"}]


def test_solve_fn_items_default_to_none_when_extraction_not_used() -> None:
    """A provider that never performed live-DOM extraction (the default
    _RawSolve.items) must surface Solution.items as None, not an empty
    list -- callers use None to mean "fall back to parsing html yourself"."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> _RawSolve:
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = CamoufoxProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.items is None
