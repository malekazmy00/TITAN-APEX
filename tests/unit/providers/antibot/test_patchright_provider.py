"""Unit tests for src/providers/antibot/patchright_provider.py.

The browser-driving call is always injected: these tests never launch a
real Patchright browser or touch the network.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import LiveDomSelectors
from src.providers.antibot.patchright_provider import (
    PatchrightProvider,
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

    provider = PatchrightProvider(solve_fn=fake_solve)

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
    ) -> _RawSolve:
        seen["post_load_wait_ms"] = post_load_wait_ms
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(post_load_wait_ms=8_000, solve_fn=fake_solve)
    provider.solve("https://example.com/")

    assert seen["post_load_wait_ms"] == 8_000


def test_non_positive_timeout_raises_antibot_error() -> None:
    """Failure case 1: a non-positive timeout is meaningless."""
    with pytest.raises(AntibotError, match="timeout_ms must be > 0"):
        PatchrightProvider(timeout_ms=0)


def test_negative_post_load_wait_ms_raises_antibot_error() -> None:
    """Failure case 2: a negative wait is meaningless."""
    with pytest.raises(AntibotError, match="post_load_wait_ms must be >= 0"):
        PatchrightProvider(post_load_wait_ms=-1)


def test_solve_function_failure_propagates_as_antibot_error() -> None:
    """Failure case 3: the browser-driving call failing (browser launch,
    navigation, ...) surfaces as AntibotError, not a raw/unexpected type --
    _default_patchright_solve itself is what wraps the real
    Patchright/Playwright exceptions; this confirms
    PatchrightProvider.solve() doesn't swallow or mistranslate whatever
    AntibotError it's given."""

    def failing_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
    ) -> _RawSolve:
        raise AntibotError(f"patchright failed to solve {url}: browser launch failed")

    provider = PatchrightProvider(solve_fn=failing_solve)

    with pytest.raises(AntibotError, match="browser launch failed"):
        provider.solve("https://example.com/")


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
    ) -> _RawSolve:
        seen["click_selector"] = click_selector
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    provider.solve("https://example.com/", click_selector="#accept-cookies")

    assert seen["click_selector"] == "#accept-cookies"


def test_zero_post_load_wait_ms_is_allowed() -> None:
    """post_load_wait_ms=0 is a legitimate (if pointless) configuration --
    not an error, unlike a negative value."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
    ) -> _RawSolve:
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(post_load_wait_ms=0, solve_fn=fake_solve)
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
    ) -> _RawSolve:
        seen["extraction_selectors"] = extraction_selectors
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
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
    ) -> _RawSolve:
        assert extraction_selectors is None
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
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
    ) -> _RawSolve:
        return _RawSolve(
            url=url,
            html="<html></html>",
            status=200,
            cookies={},
            items=[{"author": "alice"}, {"author": "bob"}],
        )

    provider = PatchrightProvider(solve_fn=fake_solve)
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
    ) -> _RawSolve:
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.items is None
