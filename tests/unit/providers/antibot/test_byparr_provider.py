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
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord
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


def test_user_agent_override_logs_a_warning_and_still_solves() -> None:
    """docs/REQUIREMENTS.md section 9 entry 24/27: a
    real, structural gap confirmed by reading Byparr's own request
    payload model (LinkRequest) -- it has no userAgent/user_agent field
    at all, the same upstream FlareSolverr-protocol limitation Byparr
    itself never adds. Passing user_agent_override must not crash or
    silently drop it; it must log clearly and still solve with its own
    real, unmodified default User-Agent."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        # Real, direct proof this is genuinely unsupported, not just
        # documented as such: the payload sent over the wire has no
        # userAgent key at all, regardless of what was requested.
        assert "userAgent" not in payload
        assert "user_agent" not in payload
        return VALID_RESPONSE

    provider = ByparrProvider(
        base_url="http://localhost:8191",
        http_post=fake_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    solution = provider.solve(
        "https://example.com/", user_agent_override="Mozilla/5.0 (custom-test-ua)"
    )

    assert solution.status_code == 200  # still solves despite the unsupported override
    message, extra = logged[0]
    assert message == "byparr_provider.user_agent_override_unsupported"
    assert extra["url"] == "https://example.com/"


def test_block_webrtc_logs_a_warning_and_still_solves() -> None:
    """docs/PHASE_2_BACKLOG.md item 5: same "must not crash or silently
    drop it" contract as user_agent_override above -- Byparr's /v1
    protocol has no browser-launch control at all."""
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

    solution = provider.solve("https://example.com/", block_webrtc=True)

    assert solution.status_code == 200  # still solves despite the unsupported request
    message, extra = logged[0]
    assert message == "byparr_provider.block_webrtc_unsupported"
    assert extra["url"] == "https://example.com/"


def test_block_webrtc_false_logs_nothing() -> None:
    """Backward compatible: every existing caller (False, the default)
    must not trigger the new warning at all."""
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

    provider.solve("https://example.com/")

    assert logged == []


def test_user_agent_override_none_logs_nothing() -> None:
    """Backward compatible: every existing caller (None, the default)
    must not trigger the new warning at all."""
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

    solution = provider.solve("https://example.com/")

    assert solution.status_code == 200
    assert logged == []


def test_base_url_trailing_slash_is_normalized() -> None:
    seen_urls: list[str] = []

    def capturing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        seen_urls.append(url)
        return VALID_RESPONSE

    provider = ByparrProvider(base_url="http://localhost:8191/", http_post=capturing_http_post)
    provider.solve("https://example.com/")

    assert seen_urls == ["http://localhost:8191/v1"]


# --- unified failure taxonomy (docs/REQUIREMENTS.md section 9 entry 28) ---


def test_request_failed_records_a_network_infra_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.providers.antibot.byparr_provider.record_failure",
        lambda record, path=None: recorded.append(record),
    )

    def failing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        raise urllib.error.URLError("connection refused")

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=failing_http_post)

    with pytest.raises(AntibotError):
        provider.solve("https://example.com/")

    assert len(recorded) == 1
    record = recorded[0]
    assert record.target == "https://example.com/"
    assert record.provider == "byparr"
    assert record.failure_category is FailureCategory.NETWORK_INFRA_TRANSIENT
    assert record.source == "byparr_provider.request_failed"


def test_invalid_json_records_an_unknown_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.providers.antibot.byparr_provider.record_failure",
        lambda record, path=None: recorded.append(record),
    )

    def bad_json_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return "{not valid json"

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=bad_json_http_post)

    with pytest.raises(AntibotError):
        provider.solve("https://example.com/")

    assert len(recorded) == 1
    assert recorded[0].failure_category is FailureCategory.UNKNOWN
    assert recorded[0].source == "byparr_provider.invalid_json"


def test_solve_failed_records_an_antibot_fingerprint_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one category with real semantic weight: byparr itself reported
    it could not solve the challenge -- a real, working target defense,
    not a bug on either side."""
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.providers.antibot.byparr_provider.record_failure",
        lambda record, path=None: recorded.append(record),
    )

    def error_status_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"status": "error", "message": "challenge not solvable"})

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=error_status_http_post)

    with pytest.raises(AntibotError):
        provider.solve("https://example.com/")

    assert len(recorded) == 1
    record = recorded[0]
    assert record.failure_category is FailureCategory.ANTIBOT_FINGERPRINT_REJECTION
    assert record.source == "byparr_provider.solve_failed"
    assert record.raw_signal["byparr_message"] == "challenge not solvable"


def test_malformed_solution_records_an_unknown_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.providers.antibot.byparr_provider.record_failure",
        lambda record, path=None: recorded.append(record),
    )

    def malformed_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"status": "ok", "solution": {"status": 200}})  # no "response" key

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=malformed_http_post)

    with pytest.raises(AntibotError):
        provider.solve("https://example.com/")

    assert len(recorded) == 1
    assert recorded[0].failure_category is FailureCategory.UNKNOWN
    assert recorded[0].source == "byparr_provider.malformed_solution"


def test_successful_solve_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: a clean solve must never write a failure record."""
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.providers.antibot.byparr_provider.record_failure",
        lambda record, path=None: recorded.append(record),
    )

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return VALID_RESPONSE

    provider = ByparrProvider(base_url="http://localhost:8191", http_post=fake_http_post)
    provider.solve("https://example.com/")

    assert recorded == []
