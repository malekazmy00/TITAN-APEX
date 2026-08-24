"""Shared scroll-to-load helper for
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider` and
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`'s
browser-driving solve functions.

Neither provider had *any* scroll capability before this -- a real,
separately-discovered gap while building the DOM Virtualization round
(docs/REQUIREMENTS.md section 9 entry 13): unlike
``src.middlewares.playwright_middleware.render_with_playwright`` (which
has had this exact scroll-to-stable loop since Phase 2), a target
protected by Anubis (``antibot_needed: true``) is solved entirely by
whichever ``AntibotProvider`` ``ByparrMiddleware`` selects --
``PlaywrightMiddleware``'s own ``process_request`` never runs at all
once an earlier middleware already returned a response, so its scroll
loop was structurally unreachable for any Anubis-protected,
infinite-scroll-shaped target (e.g. test-environment/mock-target's own
``/feed``) the whole time. This module gives Camoufox/Patchright the
identical capability ``render_with_playwright`` already has, reusing its
exact logic.

Typed loosely (``Any`` for the live Playwright/Patchright ``Page``
object) on purpose -- same tradeoff as
``src.providers.antibot._live_dom``'s own module docstring.
"""

from __future__ import annotations

from typing import Any


def scroll_to_load_lazy_content(page: Any, max_attempts: int, pause_ms: int) -> None:
    """Scroll ``page`` to the bottom repeatedly until its height stops
    growing (or ``max_attempts`` is reached) -- identical logic to
    ``playwright_middleware.py``'s own ``_scroll_to_load_lazy_content``,
    which this shadows deliberately rather than importing (that one stays
    private to its own module; duplicating ~10 lines here is cheaper than
    a cross-middleware/provider import for genuinely equivalent-but-not-
    identical browser objects).

    Harmless (a couple of no-op scroll+wait round trips) on a page that
    doesn't use infinite scroll at all -- the loop exits on its first
    iteration once height stops growing, the same justification
    ``render_with_playwright`` already documents for calling this
    unconditionally.

    Raises:
        ValueError: if ``max_attempts`` is not positive, or ``pause_ms``
            is negative -- both are meaningless configurations.
    """
    if max_attempts <= 0:
        raise ValueError(f"max_attempts must be > 0, got {max_attempts}")
    if pause_ms < 0:
        raise ValueError(f"pause_ms must be >= 0, got {pause_ms}")

    previous_height = page.evaluate("document.body.scrollHeight")
    for _ in range(max_attempts):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(pause_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height <= previous_height:
            break
        previous_height = current_height
