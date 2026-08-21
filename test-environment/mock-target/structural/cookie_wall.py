"""Cookie-consent wall: real content is genuinely absent from the response
until a consent cookie is present -- not a CSS-hidden overlay.

Deliberately hard, not cosmetic: a consent banner that just sits on top of
content that's already in the DOM is trivially defeated by any
selector-based scraper that never checks visibility at all (the same gap
structural/honeypots.py documents for honeypot links) -- that would teach
nothing new (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's own point:
many real sites' consent walls genuinely gate the underlying content
server-side, not just visually). So this module's gate is server-side:
no consent cookie, no real posts anywhere in the response body.
"""

from __future__ import annotations

CONSENT_COOKIE_NAME = "cookie_consent"
CONSENT_COOKIE_VALUE = "accepted"
ACCEPT_PATH = "/accept-cookies"


def has_consent(cookie_value: str | None) -> bool:
    """Whether an incoming request already carries a valid consent cookie."""
    return cookie_value == CONSENT_COOKIE_VALUE
