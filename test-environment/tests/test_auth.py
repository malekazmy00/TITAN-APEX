"""Unit tests for security/auth.py -- login/session challenge primitives
(docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Known Limitation #1)."""

from __future__ import annotations

import pytest
from security.auth import (
    TEST_PASSWORD,
    TEST_USERNAME,
    CsrfTokenStore,
    SessionStore,
    check_credentials,
)

# --- check_credentials ---------------------------------------------------


def test_check_credentials_accepts_the_real_fixed_test_credentials() -> None:
    assert check_credentials(TEST_USERNAME, TEST_PASSWORD) is True


def test_check_credentials_rejects_a_wrong_password() -> None:
    assert check_credentials(TEST_USERNAME, "wrong") is False


def test_check_credentials_rejects_a_wrong_username() -> None:
    assert check_credentials("wrong", TEST_PASSWORD) is False


def test_check_credentials_rejects_both_empty() -> None:
    assert check_credentials("", "") is False


# --- CsrfTokenStore --------------------------------------------------------


def test_csrf_issue_produces_a_non_empty_token() -> None:
    store = CsrfTokenStore()
    token = store.issue()
    assert token
    assert isinstance(token, str)


def test_csrf_issue_produces_a_different_token_every_time() -> None:
    """The real requirement this whole module exists for: a token must
    change on every GET /login load, never be fixed."""
    store = CsrfTokenStore()
    assert store.issue() != store.issue()


def test_csrf_consume_accepts_a_live_token_once() -> None:
    store = CsrfTokenStore()
    token = store.issue()
    assert store.consume(token) is True


def test_csrf_consume_rejects_the_same_token_a_second_time() -> None:
    """Real replay protection: a captured token can't be reused."""
    store = CsrfTokenStore()
    token = store.issue()
    store.consume(token)
    assert store.consume(token) is False


def test_csrf_consume_rejects_an_unknown_token() -> None:
    store = CsrfTokenStore()
    assert store.consume("never-issued") is False


def test_csrf_consume_rejects_none() -> None:
    store = CsrfTokenStore()
    assert store.consume(None) is False


def test_csrf_consume_rejects_empty_string() -> None:
    store = CsrfTokenStore()
    assert store.consume("") is False


# --- SessionStore ----------------------------------------------------------


def test_session_issue_produces_a_non_empty_token() -> None:
    store = SessionStore(ttl_seconds=300)
    token = store.issue("alice")
    assert token
    assert isinstance(token, str)


def test_session_issue_rejects_empty_username() -> None:
    store = SessionStore(ttl_seconds=300)
    with pytest.raises(ValueError, match="username must be non-empty"):
        store.issue("")


def test_session_is_valid_true_for_a_freshly_issued_token() -> None:
    store = SessionStore(ttl_seconds=300)
    token = store.issue("alice")
    assert store.is_valid(token) is True


def test_session_is_valid_false_for_none() -> None:
    store = SessionStore(ttl_seconds=300)
    assert store.is_valid(None) is False


def test_session_is_valid_false_for_an_unknown_token() -> None:
    store = SessionStore(ttl_seconds=300)
    assert store.is_valid("never-issued") is False


def test_session_expires_after_ttl_elapses_deterministic_fake_clock() -> None:
    """No real sleep -- an injectable clock, same pattern as
    structural/feed.py's own FeedRateLimiter, proves expiry
    deterministically."""
    now = [1000.0]
    store = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    token = store.issue("alice")
    assert store.is_valid(token) is True

    now[0] += 11  # past the 10s TTL
    assert store.is_valid(token) is False


def test_session_still_valid_just_before_ttl_elapses() -> None:
    now = [1000.0]
    store = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    token = store.issue("alice")

    now[0] += 9
    assert store.is_valid(token) is True


def test_session_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        SessionStore(ttl_seconds=0)


def test_session_force_expire_makes_a_valid_token_invalid() -> None:
    store = SessionStore(ttl_seconds=300)
    token = store.issue("alice")
    assert store.is_valid(token) is True

    assert store.force_expire(token) is True
    assert store.is_valid(token) is False


def test_session_force_expire_returns_false_for_an_unknown_token() -> None:
    store = SessionStore(ttl_seconds=300)
    assert store.force_expire("never-issued") is False


def test_session_force_expire_returns_false_for_none() -> None:
    store = SessionStore(ttl_seconds=300)
    assert store.force_expire(None) is False


def test_two_sessions_from_the_same_username_are_independent() -> None:
    """Each issue() call is its own session -- expiring one must not
    affect the other."""
    store = SessionStore(ttl_seconds=300)
    token_a = store.issue("alice")
    token_b = store.issue("alice")

    store.force_expire(token_a)

    assert store.is_valid(token_a) is False
    assert store.is_valid(token_b) is True
