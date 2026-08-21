"""Hidden trap links -- no real human ever sees or clicks these.

Each honeypot carries a unique token in its URL
(``/honeypot-trap/<token>``); a hit on that route (security/honeypot_logger.py)
is unambiguous proof a scraper interacted with something invisible instead
of respecting visibility, which is a real, currently-uncovered gap in
GenericSpider (it extracts from every CSS match with no visibility check
at all).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

# Every way real sites hide an element from a human but leave it in the DOM
# (and therefore in anything that parses raw HTML/CSS without a real
# rendered layout).
HIDE_METHODS = (
    "display-none",
    "visibility-hidden",
    "opacity-offscreen",
    "aria-hidden",
)


@dataclass
class HoneypotLink:
    token: str
    url: str
    hide_method: str
    label: str


def generate_honeypot_links(
    count: int = 4, token_factory: Callable[[], str] | None = None
) -> list[HoneypotLink]:
    """Build ``count`` honeypot links, cycling through every hide method.

    ``token_factory`` is an injectable zero-arg callable returning a token
    string, defaulting to ``secrets.token_hex`` -- deterministic tokens in
    tests, unpredictable ones for real.

    Raises:
        ValueError: if ``count`` is not positive.
    """
    if count <= 0:
        raise ValueError(f"count must be > 0, got {count}")

    make_token = token_factory if token_factory is not None else lambda: secrets.token_hex(8)

    links = []
    for i in range(count):
        token = make_token()
        hide_method = HIDE_METHODS[i % len(HIDE_METHODS)]
        links.append(
            HoneypotLink(
                token=token,
                url=f"/honeypot-trap/{token}",
                hide_method=hide_method,
                label=f"See more #{i + 1}",
            )
        )
    return links
