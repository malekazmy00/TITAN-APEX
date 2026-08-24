"""Login/session challenge: real POST-based login, a CSRF token that
changes on every ``GET /login`` load (never fixed/hardcodable), and a
real, TTL-based server-side session -- docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
Known Limitation #1 (login/session), activated ahead of Interstitials per
the user's explicit request.

Deliberately hard, not cosmetic, same philosophy as
``structural/cookie_wall.py``: the CSRF token is single-use (consumed the
moment a POST successfully uses it, so a scraper can never replay a
captured token -- it must genuinely re-fetch ``GET /login`` for every
attempt), and the session is validated server-side with a real
elapsed-time check, not just "cookie present = trust it".

Scope, deliberately bounded (per the user's own explicit instruction --
anything beyond this, e.g. 2FA, is a separate, later backlog item, not
solved here): username/password + CSRF + session issuance/validation/
expiry. No password hashing, no rate limiting on login attempts, no
account lockout -- this is a scraping-technique training target, not a
real auth system, and those are orthogonal concerns this round isn't
about.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable

# Fixed, publicly-documented test credentials -- not a real secret, not a
# real account. Anyone running this training target already has the
# source code that defines them.
TEST_USERNAME = "titan_test_user"
TEST_PASSWORD = "titan_test_pass"  # noqa: S105 -- fixed test credential, not a real secret

AUTH_SESSION_COOKIE_NAME = "mocktarget_auth_session"
CSRF_FIELD_NAME = "csrf_token"
USERNAME_FIELD_NAME = "username"
PASSWORD_FIELD_NAME = "password"


def check_credentials(username: str, password: str) -> bool:
    """Constant-time comparison against the fixed test credentials.

    ``hmac.compare_digest`` avoids a timing side channel -- a real
    practice worth exercising even on a training target, not just a toy
    ``==`` check (same "the real thing, not a cosmetic stand-in"
    philosophy ``structural/cookie_wall.py``/``structural/shadow_dom.py``
    already have for their own layers)."""
    return hmac.compare_digest(username, TEST_USERNAME) and hmac.compare_digest(
        password, TEST_PASSWORD
    )


class CsrfTokenStore:
    """Tracks live (not-yet-consumed) CSRF tokens.

    Every ``GET /login`` issues a fresh token via :meth:`issue` -- never
    a fixed/static value, so a scraper cannot hardcode one it captured
    once. A token is consumed (removed, single-use) the moment a POST
    successfully uses it via :meth:`consume`, the same real
    replay-protection shape a genuine anti-CSRF token has: capturing a
    token from one page load and reusing it twice must fail the second
    time.
    """

    def __init__(self) -> None:
        self._live_tokens: set[str] = set()

    def issue(self) -> str:
        token = secrets.token_hex(16)
        self._live_tokens.add(token)
        return token

    def consume(self, token: str | None) -> bool:
        """``True`` (and removes the token) if ``token`` was live;
        ``False`` (no removal) if it's missing, unknown, or already
        consumed."""
        if not token or token not in self._live_tokens:
            return False
        self._live_tokens.discard(token)
        return True


class SessionStore:
    """In-memory, TTL-based session store.

    Real production expiry is always elapsed-time-based, checked against
    an injectable clock (same "fake clock, not real sleep" pattern
    ``structural/feed.py``'s own ``FeedRateLimiter`` already uses, so
    unit tests can assert expiry deterministically without ever actually
    waiting). :meth:`force_expire` is a *separate*, deliberately
    test-only escape hatch (see ``/test-expire-session`` in ``app.py``)
    for live-CI tests that need to trigger session-expiry *detection*
    without a real, flaky multi-second wait -- it never gets called by
    any real login/session-check code path, only by that one dedicated
    test route.
    """

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] | None = None) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.time
        self._issued_at: dict[str, float] = {}

    def issue(self, username: str) -> str:
        """Issues a fresh session token for ``username``.

        Raises:
            ValueError: if ``username`` is empty -- there is no session
                to attribute to nobody.
        """
        if not username:
            raise ValueError("username must be non-empty")
        token = secrets.token_hex(16)
        self._issued_at[token] = self._clock()
        return token

    def is_valid(self, token: str | None) -> bool:
        """Whether ``token`` is a known session that hasn't yet expired."""
        if not token or token not in self._issued_at:
            return False
        return (self._clock() - self._issued_at[token]) < self._ttl_seconds

    def force_expire(self, token: str | None) -> bool:
        """Test-only: deterministically back-dates ``token`` past its own
        TTL, so the very next :meth:`is_valid` call returns ``False`` --
        see this class's own docstring for why this exists at all.
        ``True`` if ``token`` was a known session (now expired); ``False``
        if it was missing/unknown (nothing to expire)."""
        if not token or token not in self._issued_at:
            return False
        self._issued_at[token] = self._clock() - self._ttl_seconds - 1
        return True
