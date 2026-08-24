"""Unit tests for src/providers/antibot/_login.py.

A fake Page records .goto()/.fill()/.click() calls in order -- no real
browser involved (docs/REQUIREMENTS.md section 9 entry 15).
"""

from __future__ import annotations

import pytest

from src.providers.antibot._login import (
    log_login_outcome,
    perform_login_and_navigate,
    submit_login_form,
)


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.wait_calls: list[int] = []

    def goto(self, url: str, timeout: int) -> None:
        self.calls.append(("goto", url, str(timeout)))

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))

    def click(self, selector: str, timeout: int) -> None:
        self.calls.append(("click", selector, str(timeout)))

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls.append(ms)


def test_submits_the_form_in_the_right_order() -> None:
    """Happy path: navigate, fill username, fill password, click submit
    -- in exactly that order, matching a real user's own sequence."""
    page = _FakePage()

    submit_login_form(
        page,
        login_url="http://localhost:8080/login",
        username="titan_test_user",
        password="titan_test_pass",
        username_field="#username",
        password_field="#password",
        submit_selector="#login-submit",
        timeout_ms=30_000,
    )

    assert page.calls == [
        ("goto", "http://localhost:8080/login", "30000"),
        ("fill", "#username", "titan_test_user"),
        ("fill", "#password", "titan_test_pass"),
        ("click", "#login-submit", "30000"),
    ]


def test_rejects_an_empty_login_url() -> None:
    with pytest.raises(ValueError, match="login_url must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="",
            username="u",
            password="p",
            username_field="#u",
            password_field="#p",
            submit_selector="#s",
            timeout_ms=30_000,
        )


def test_rejects_an_empty_username() -> None:
    with pytest.raises(ValueError, match="username must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="http://localhost:8080/login",
            username="",
            password="p",
            username_field="#u",
            password_field="#p",
            submit_selector="#s",
            timeout_ms=30_000,
        )


def test_rejects_an_empty_password() -> None:
    with pytest.raises(ValueError, match="password must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="http://localhost:8080/login",
            username="u",
            password="",
            username_field="#u",
            password_field="#p",
            submit_selector="#s",
            timeout_ms=30_000,
        )


def test_rejects_an_empty_username_field_selector() -> None:
    with pytest.raises(ValueError, match="username_field must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="http://localhost:8080/login",
            username="u",
            password="p",
            username_field="",
            password_field="#p",
            submit_selector="#s",
            timeout_ms=30_000,
        )


def test_rejects_an_empty_password_field_selector() -> None:
    with pytest.raises(ValueError, match="password_field must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="http://localhost:8080/login",
            username="u",
            password="p",
            username_field="#u",
            password_field="",
            submit_selector="#s",
            timeout_ms=30_000,
        )


def test_rejects_an_empty_submit_selector() -> None:
    with pytest.raises(ValueError, match="submit_selector must be non-empty"):
        submit_login_form(
            _FakePage(),
            login_url="http://localhost:8080/login",
            username="u",
            password="p",
            username_field="#u",
            password_field="#p",
            submit_selector="",
            timeout_ms=30_000,
        )


# --- perform_login_and_navigate (docs/REQUIREMENTS.md section 9 entry
# 15) --------------------------------------------------------------


def test_successful_login_visits_target_url_after_submitting() -> None:
    """Happy path: submit the form, then navigate straight to
    target_url when the login POST itself succeeded (no session-expiry
    probe configured)."""
    page = _FakePage()

    result = perform_login_and_navigate(
        page,
        login_url="http://localhost:8080/login",
        username="titan_test_user",
        password="titan_test_pass",
        username_field="#username",
        password_field="#password",
        submit_selector="#login-submit",
        timeout_ms=30_000,
        post_load_wait_ms=5_000,
        get_last_status=lambda: 200,  # both the login-check and final reads see this
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url=None,
    )

    assert result == (True, 200)
    assert page.calls == [
        ("goto", "http://localhost:8080/login", "30000"),
        ("fill", "#username", "titan_test_user"),
        ("fill", "#password", "titan_test_pass"),
        ("click", "#login-submit", "30000"),
        ("goto", "http://localhost:8080/feed-protected", "30000"),
    ]
    assert page.wait_calls == [5_000]


def test_a_missing_status_after_submit_is_treated_as_success() -> None:
    """Same "no response object at all isn't itself a failure" contract
    the rest of this codebase already has for a real navigation
    response."""
    page = _FakePage()

    result = perform_login_and_navigate(
        page,
        login_url="http://localhost:8080/login",
        username="u",
        password="p",
        username_field="#u",
        password_field="#p",
        submit_selector="#s",
        timeout_ms=30_000,
        post_load_wait_ms=5_000,
        get_last_status=lambda: None,
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url=None,
    )

    assert result == (True, None)


def test_failed_login_does_not_navigate_anywhere_else() -> None:
    """Failure-adjacent case 1: a real 401/403 (wrong credentials, a
    stale/replayed CSRF token) stops here -- target_url is never
    visited, and neither is any session-expiry probe."""
    page = _FakePage()

    result = perform_login_and_navigate(
        page,
        login_url="http://localhost:8080/login",
        username="u",
        password="wrong",
        username_field="#u",
        password_field="#p",
        submit_selector="#s",
        timeout_ms=30_000,
        post_load_wait_ms=5_000,
        get_last_status=lambda: 401,
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url="http://localhost:8080/test-expire-session",
    )

    assert result == (False, 401)
    assert page.calls == [
        ("goto", "http://localhost:8080/login", "30000"),
        ("fill", "#u", "u"),
        ("fill", "#p", "wrong"),
        ("click", "#s", "30000"),
    ]


def test_successful_login_visits_the_session_expiry_probe_before_the_target() -> None:
    """The test-only knob (see LoginFlow's own docstring): when given,
    visited once, in between the login and the real target -- never on
    a failed login."""
    page = _FakePage()

    perform_login_and_navigate(
        page,
        login_url="http://localhost:8080/login",
        username="u",
        password="p",
        username_field="#u",
        password_field="#p",
        submit_selector="#s",
        timeout_ms=30_000,
        post_load_wait_ms=5_000,
        get_last_status=lambda: 302,
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url="http://localhost:8080/test-expire-session",
    )

    goto_urls = [call[1] for call in page.calls if call[0] == "goto"]
    assert goto_urls == [
        "http://localhost:8080/login",
        "http://localhost:8080/test-expire-session",
        "http://localhost:8080/feed-protected",
    ]


def test_final_status_reflects_the_state_after_the_probe_and_target_navigation() -> None:
    """The real reason perform_login_and_navigate returns a status at
    all: a login that succeeded can still end up rejected by the time
    the real target is actually reached (e.g. the session-expiry probe
    deliberately invalidated it in between) -- final_status must reflect
    *that* later read, not the (successful) status right after the login
    POST itself."""
    page = _FakePage()
    statuses = iter([200, 401])  # right after login POST, then after probe+target

    result = perform_login_and_navigate(
        page,
        login_url="http://localhost:8080/login",
        username="u",
        password="p",
        username_field="#u",
        password_field="#p",
        submit_selector="#s",
        timeout_ms=30_000,
        post_load_wait_ms=5_000,
        get_last_status=lambda: next(statuses),
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url="http://localhost:8080/test-expire-session",
    )

    assert result == (True, 401)  # login itself succeeded; final read shows it's now expired


# --- log_login_outcome (docs/REQUIREMENTS.md section 9 entry 15) -------


class _FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict[str, object]]] = []
        self.warning_calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, extra: dict[str, object]) -> None:
        self.info_calls.append((event, extra))

    def warning(self, event: str, extra: dict[str, object]) -> None:
        self.warning_calls.append((event, extra))


def test_log_login_outcome_success_logs_only_login_succeeded() -> None:
    """Happy path: a clean success (status < 400 after the target
    navigation) logs exactly one info event, no warning."""
    logger = _FakeLogger()

    log_login_outcome(
        logger,
        "camoufox_provider",
        login_url="http://localhost:8080/login",
        target_url="http://localhost:8080/feed-protected",
        login_ok=True,
        final_status=200,
    )

    assert logger.info_calls == [
        (
            "camoufox_provider.login_succeeded",
            {"login_url": "http://localhost:8080/login"},
        )
    ]
    assert logger.warning_calls == []


def test_log_login_outcome_success_then_expired_logs_both_events() -> None:
    """The session-expiry-detection case: login succeeded (info), but
    the final read is a real 401/403 -- a second, distinct warning event
    fires too, not just the success log."""
    logger = _FakeLogger()

    log_login_outcome(
        logger,
        "patchright_provider",
        login_url="http://localhost:8080/login",
        target_url="http://localhost:8080/feed-protected",
        login_ok=True,
        final_status=401,
    )

    assert logger.info_calls == [
        (
            "patchright_provider.login_succeeded",
            {"login_url": "http://localhost:8080/login"},
        )
    ]
    assert logger.warning_calls == [
        (
            "patchright_provider.session_expired_mid_crawl",
            {"url": "http://localhost:8080/feed-protected", "status": 401},
        )
    ]


def test_log_login_outcome_failure_logs_only_login_failed() -> None:
    """Failure-adjacent case: a failed login logs exactly one warning
    event (login_failed), never login_succeeded and never
    session_expired_mid_crawl (that event is specifically for a login
    that *did* succeed)."""
    logger = _FakeLogger()

    log_login_outcome(
        logger,
        "camoufox_provider",
        login_url="http://localhost:8080/login",
        target_url="http://localhost:8080/feed-protected",
        login_ok=False,
        final_status=401,
    )

    assert logger.info_calls == []
    assert logger.warning_calls == [
        (
            "camoufox_provider.login_failed",
            {"login_url": "http://localhost:8080/login", "status": 401},
        )
    ]
