"""Unit tests for src/providers/antibot/byparr_provider.py.

The HTTP transport is always injected: these tests never touch a real
network or a real Byparr instance.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import LiveDomSelectors, LoginFlow
from src.providers.antibot.byparr_provider import ByparrProvider

VALID_RESPONSE = json.dumps(
    {
        "status": "ok",
        "message": "",
        "solution": {
            "url": "https://example.com/",
            "status": 200,
            "response": "<html><body>solved</body></html>",
            "cookies": [{"name": "cf_clearance", "value": "abc123"}],
        },
    }
)


def test_solve_returns_a_populated_solution() -> None:
    """Happy path: a successful Byparr response yields a full Solution."""

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        assert payload["cmd"] == "request.get"
        assert payload["url"] == "https://example.com/"
        return VALID_RESPONSE

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=fake_http_post)

    solution = provider.solve("https://example.com/")

    assert solution.html == "<html><body>solved</body></html>"
    assert solution.status_code == 200
    assert solution.cookies == {"cf_clearance": "abc123"}


def test_empty_base_url_raises_antibot_error() -> None:
    with pytest.raises(AntibotError, match="non-empty base_url"):
        ByparrProvider(base_url="")


def test_connection_failure_raises_antibot_error() -> None:
    """Failure case 1: a transport-level connection error is wrapped, not raw."""

    def failing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        raise urllib.error.URLError("connection refused")

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=failing_http_post)

    with pytest.raises(AntibotError, match="byparr request failed"):
        provider.solve("https://example.com/")


def test_invalid_json_raises_antibot_error() -> None:
    """Failure case 2: a corrupted/non-JSON response is wrapped, not raw."""

    def bad_json_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return "{not valid json"

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=bad_json_http_post)

    with pytest.raises(AntibotError, match="invalid JSON"):
        provider.solve("https://example.com/")


def test_error_status_raises_antibot_error() -> None:
    """Failure case 3: Byparr itself reporting failure (status != 'ok') is not silent."""

    def error_status_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"status": "error", "message": "challenge not solvable"})

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=error_status_http_post)

    with pytest.raises(AntibotError, match="challenge not solvable"):
        provider.solve("https://example.com/")


def test_malformed_solution_raises_antibot_error() -> None:
    """Failure case 4: a well-formed 'ok' response missing expected fields is rejected."""

    def malformed_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"status": "ok", "solution": {"status": 200}})  # no "response" key

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=malformed_http_post)

    with pytest.raises(AntibotError, match="missing expected fields"):
        provider.solve("https://example.com/")


def test_click_selector_logs_a_warning_and_still_solves() -> None:
    """Byparr's /v1 API has no click/interact capability
    (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md, cookie-consent-wall
    round) -- passing click_selector must not crash or silently drop it;
    it must log clearly and still solve without clicking."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    solution = provider.solve("https://example.com/", click_selector="#accept-cookies")

    assert solution.status_code == 200  # still solves despite the unsupported click
    message, extra = logged[0]
    assert message == "byparr_provider.click_selector_unsupported"
    assert extra["click_selector"] == "#accept-cookies"


def test_extraction_selectors_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 12: Byparr's /v1 API returns
    HTML only, no live page for GenericSpider (or anything else) to query
    -- passing extraction_selectors must not crash or silently drop it;
    it must log clearly and still solve without live-DOM extraction."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    selectors = LiveDomSelectors(item='[data-role="post"]', fields={"author": "::text"})
    solution = provider.solve("https://example.com/", extraction_selectors=selectors)

    assert solution.status_code == 200  # still solves despite the unsupported extraction
    assert solution.items is None  # no live-DOM extraction actually happened
    message, extra = logged[0]
    assert message == "byparr_provider.extraction_selectors_unsupported"
    assert extra["url"] == "https://example.com/"


def test_login_flow_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 15: Byparr's /v1 API has no
    form-fill/interact capability at all -- passing login_flow must not
    crash or silently drop it; it must log clearly and still solve
    without ever attempting the login."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    login_flow = LoginFlow(
        login_url="https://example.com/login",
        username="alice",
        password="secret",
        username_field="#username",
        password_field="#password",
        submit_selector="#submit",
    )
    solution = provider.solve("https://example.com/", login_flow=login_flow)

    assert solution.status_code == 200  # still solves despite the unsupported login
    message, extra = logged[0]
    assert message == "byparr_provider.login_flow_unsupported"
    assert extra["url"] == "https://example.com/"


def test_warm_session_urls_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 2: Byparr's /v1 API
    has no live page to navigate a warm-up chain through -- passing
    warm_session_urls must not crash or silently drop it; it must log
    clearly and still solve without ever attempting the warm-up."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    solution = provider.solve(
        "https://example.com/", warm_session_urls=["https://example.com/category"]
    )

    assert solution.status_code == 200  # still solves despite the unsupported warm-up
    message, extra = logged[0]
    assert message == "byparr_provider.warm_session_urls_unsupported"
    assert extra["url"] == "https://example.com/"


def test_use_accumulated_profile_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 2: Byparr's /v1 API
    has no browser context to load/save a profile into -- passing
    use_accumulated_profile must not crash or silently drop it; it must
    log clearly and still solve with no profile applied."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    solution = provider.solve("https://example.com/", use_accumulated_profile=True)

    assert solution.status_code == 200  # still solves despite the unsupported profile
    message, extra = logged[0]
    assert message == "byparr_provider.use_accumulated_profile_unsupported"
    assert extra["url"] == "https://example.com/"


def test_progressive_extraction_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 14: Byparr's /v1 API returns
    HTML only, no live page to scroll -- passing progressive_extraction
    must not crash or silently drop it; it must log clearly and still
    solve via a single, non-progressive read."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    solution = provider.solve("https://example.com/", progressive_extraction=True)

    assert solution.status_code == 200  # still solves despite the unsupported progression
    assert solution.html_snapshots is None  # no progressive collection actually happened
    message, extra = logged[0]
    assert message == "byparr_provider.progressive_extraction_unsupported"
    assert extra["url"] == "https://example.com/"


def test_base_url_trailing_slash_is_normalized() -> None:
    seen_urls: list[str] = []

    def capturing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        seen_urls.append(url)
        return VALID_RESPONSE

    provider = ByparrProvider(base_url="http://localhost:8191/", http_post=capturing_http_post)
    provider.solve("https://example.com/")

    assert seen_urls == ["http://localhost:8191/v1"]
