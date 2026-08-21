"""Unit tests for mock-target/structural/cookie_wall.py."""

from __future__ import annotations

from structural.cookie_wall import CONSENT_COOKIE_VALUE, has_consent


def test_has_consent_true_for_the_real_value() -> None:
    """Happy path: the exact consent value the app itself sets is honored."""
    assert has_consent(CONSENT_COOKIE_VALUE) is True


def test_has_consent_false_when_cookie_missing() -> None:
    """Failure case 1: no cookie at all (a fresh visitor) means no consent."""
    assert has_consent(None) is False


def test_has_consent_false_for_an_unexpected_value() -> None:
    """Failure case 2: a stray/forged cookie value is not accepted as consent."""
    assert has_consent("yes-i-guess") is False
