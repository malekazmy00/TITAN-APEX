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


def test_base_url_trailing_slash_is_normalized() -> None:
    seen_urls: list[str] = []

    def capturing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        seen_urls.append(url)
        return VALID_RESPONSE

    provider = ByparrProvider(base_url="http://localhost:8191/", http_post=capturing_http_post)
    provider.solve("https://example.com/")

    assert seen_urls == ["http://localhost:8191/v1"]
