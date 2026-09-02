"""Unit tests for src/providers/antibot/patchright_provider.py.

The browser-driving call is always injected: these tests never launch a
real Patchright browser or touch the network.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AntibotError, BrowserCrashedError
from src.core.interfaces.antibot_provider import LiveDomSelectors, LoginFlow
from src.providers.antibot.patchright_provider import (
    PatchrightProvider,
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
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        raise AntibotError(f"patchright failed to solve {url}: browser launch failed")

    provider = PatchrightProvider(solve_fn=failing_solve)

    with pytest.raises(AntibotError, match="browser launch failed"):
        provider.solve("https://example.com/")


def test_classify_solve_exception_returns_browser_crashed_error_when_a_real_crash_fired() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17: same direct test of the
    real classification decision as camoufox_provider.py's identical
    test -- see its own docstring for the full reasoning."""
    result = _classify_solve_exception(
        browser_crashed=True, url="https://example.com/", exc=Exception("Target closed")
    )

    assert isinstance(result, BrowserCrashedError)
    assert "browser engine crashed" in str(result)


def test_classify_solve_exception_returns_plain_antibot_error_when_no_crash_fired() -> None:
    result = _classify_solve_exception(
        browser_crashed=False, url="https://example.com/", exc=Exception("denied")
    )

    assert type(result) is AntibotError
    assert not isinstance(result, BrowserCrashedError)


def test_browser_crash_retries_on_a_fresh_call_and_eventually_succeeds() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17: same bounded
    browser-crash retry as CamoufoxProvider's identical behavior -- see
    its own test of the same name for the full reasoning."""
    calls = 0

    def flaky_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise BrowserCrashedError(f"patchright's browser engine crashed mid-solve for {url}")
        return _RawSolve(url=url, html="<html>solved</html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=flaky_solve, max_browser_crash_attempts=3)

    solution = provider.solve("https://example.com/")

    assert calls == 3
    assert solution.html == "<html>solved</html>"


def test_browser_crash_exhausts_max_attempts_and_raises() -> None:
    """Bounded, not open-ended."""
    calls = 0

    def always_crashes(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        raise BrowserCrashedError(f"patchright's browser engine crashed mid-solve for {url}")

    provider = PatchrightProvider(solve_fn=always_crashes, max_browser_crash_attempts=3)

    with pytest.raises(BrowserCrashedError):
        provider.solve("https://example.com/")

    assert calls == 3


def test_non_crash_antibot_error_is_not_retried() -> None:
    """A real, reproducible solve failure that isn't a browser crash must
    not be retried the same way."""
    calls = 0

    def denied_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        nonlocal calls
        calls += 1
        raise AntibotError(f"patchright failed to solve {url}: denied")

    provider = PatchrightProvider(solve_fn=denied_solve, max_browser_crash_attempts=3)

    with pytest.raises(AntibotError, match="denied"):
        provider.solve("https://example.com/")

    assert calls == 1


def test_non_positive_max_browser_crash_attempts_raises_antibot_error() -> None:
    """Failure case: a non-positive retry bound is meaningless."""
    with pytest.raises(AntibotError, match="max_browser_crash_attempts must be > 0"):
        PatchrightProvider(max_browser_crash_attempts=0)


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
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
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
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.items is None


# --- docs/REQUIREMENTS.md section 9 entry 21, Step 2 (persistent
# context across warm-up + a cross-call accumulated cookie jar) --------


def test_warm_session_urls_reaches_the_solve_function() -> None:
    seen: dict[str, list[str] | None] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        seen["warm_session_urls"] = warm_session_urls
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    provider.solve(
        "https://example.com/", warm_session_urls=["https://example.com/", "https://example.com/category"]
    )

    assert seen["warm_session_urls"] == [
        "https://example.com/",
        "https://example.com/category",
    ]


def test_warm_session_urls_defaults_to_none_when_not_given() -> None:
    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        assert warm_session_urls is None
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200


def test_use_accumulated_profile_reaches_the_solve_function() -> None:
    seen: dict[str, bool] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        seen["use_accumulated_profile"] = use_accumulated_profile
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    provider.solve("https://example.com/", use_accumulated_profile=True)

    assert seen["use_accumulated_profile"] is True


def test_use_accumulated_profile_defaults_to_false_when_not_given() -> None:
    """Backward compatible: every existing caller (including entry 17's
    own test suite, which depends on complete isolation between calls)
    must keep getting a genuinely fresh, empty profile by default."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        assert use_accumulated_profile is False
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200


def test_cookie_jar_path_reaches_the_solve_function() -> None:
    """The provider *instance's* own configured path, not a per-call
    default -- docs/REQUIREMENTS.md section 9 entry 21, Step 2."""
    seen: dict[str, str] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        seen["cookie_jar_path"] = cookie_jar_path
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve, cookie_jar_path="/tmp/my-jar.json")
    provider.solve("https://example.com/")

    assert seen["cookie_jar_path"] == "/tmp/my-jar.json"


def test_user_agent_override_reaches_the_solve_function() -> None:
    """docs/REQUIREMENTS.md section 9 entry 24/27: the
    whole point of this parameter -- a per-call override reaches the
    real browser-driving call verbatim."""
    seen: dict[str, str | None] = {}

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        seen["user_agent_override"] = user_agent_override
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    provider.solve("https://example.com/", user_agent_override="Mozilla/5.0 (custom-test-ua)")

    assert seen["user_agent_override"] == "Mozilla/5.0 (custom-test-ua)"


def test_user_agent_override_defaults_to_none_when_not_given() -> None:
    """Backward compatible: every existing caller keeps getting the
    provider's own real default User-Agent, unchanged."""

    def fake_solve(
        url: str,
        timeout_ms: int,
        post_load_wait_ms: int,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        cookie_jar_path: str = "unused-in-tests.json",
        user_agent_override: str | None = None,
    ) -> _RawSolve:
        assert user_agent_override is None
        return _RawSolve(url=url, html="<html></html>", status=200, cookies={})

    provider = PatchrightProvider(solve_fn=fake_solve)
    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200
