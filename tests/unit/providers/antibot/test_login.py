"""Unit tests for src/providers/antibot/_login.py.

A fake Page records .goto()/.fill()/.click() calls in order -- no real
browser involved (docs/REQUIREMENTS.md section 9 entry 15).
"""

from __future__ import annotations

import pytest

from src.providers.antibot._login import perform_login_and_navigate, submit_login_form


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
        get_last_status=lambda: 302,  # a real login redirect
        target_url="http://localhost:8080/feed-protected",
        session_expiry_probe_url=None,
    )

    assert result is True
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

    assert result is True


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

    assert result is False
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
