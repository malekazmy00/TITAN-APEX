"""Patchright implementation of the :class:`AntibotProvider` interface.

Patchright (https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python,
Apache-2.0) is a real, drop-in replacement for Playwright's own Python API
(``from patchright.sync_api import sync_playwright`` -- same
``p.chromium.launch()`` / ``browser.new_page()`` / ``page.goto()`` shape as
plain Playwright, confirmed against its own PyPI metadata and README before
writing this) that patches Chromium's own automation fingerprints out at
the driver level. It is Chromium-only (no Firefox/Webkit) and needs its
own separate patched-Chromium binary (``patchright install chromium`` --
does *not* reuse a plain-Playwright-installed Chromium).

This is the "lighter than Camoufox" option
(:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider`'s own
module docstring forward-references this module): built on the exact same
idea -- drive the browser in-process and hold it open past the page's
``load`` event so an async, post-load challenge (e.g. Anubis's real
proof-of-work flow) gets a real chance to finish, the one thing
:class:`~src.providers.antibot.byparr_provider.ByparrProvider`'s external
HTTP API structurally cannot do -- but reusing the Playwright-shaped
Chromium automation this project already depends on
(``playwright_middleware.py``) plus Patchright as a stealth layer on top of
it, instead of a whole separate Firefox-based stealth browser. Whether
"lighter" also means "weaker against this stack's real Anubis challenge"
is not assumed here -- see docs/REQUIREMENTS.md's "Antibot Provider
Comparison" table for the actual, CI-confirmed result once this provider
has been run against it for real.

The actual browser-driving call is injectable (``solve_fn``) so unit tests
never launch a real browser or touch the network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from typing import NamedTuple

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider, Solution
from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 30_000
# Same reasoning and same default as CamoufoxProvider's
# DEFAULT_POST_LOAD_WAIT_MS (camoufox_provider.py) -- long enough for
# Anubis's own real, difficulty-2-by-default proof-of-work challenge to
# compute and round-trip.
DEFAULT_POST_LOAD_WAIT_MS = 5_000


class _RawSolve(NamedTuple):
    """What a real browser-driving call actually produces."""

    url: str
    html: str
    status: int
    cookies: dict[str, str]


# (url, timeout_ms, post_load_wait_ms, click_selector) -> raw browser result
PatchrightSolveFn = Callable[[str, int, int, "str | None"], _RawSolve]


def _default_patchright_solve(
    url: str, timeout_ms: int, post_load_wait_ms: int, click_selector: str | None = None
) -> _RawSolve:
    """Drive a real Patchright-stealthed Chromium: navigate, (optionally)
    click, wait past ``load``, read, close.

    ``click_selector``: same reasoning as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameter -- this provider drives a real Playwright-shaped
    ``Page`` too, so it can genuinely click. ``post_load_wait_ms`` (already
    configurable) is reused as the "wait after click" delay, not a new
    parameter.

    Raises:
        AntibotError: if the browser fails to launch, navigate, or read
            the page -- wraps Patchright's own launch/navigation/page
            errors (``patchright.sync_api.Error``, the same exception type
            plain Playwright raises, since Patchright is a drop-in
            replacement for it).
    """
    from patchright.sync_api import Error as PatchrightError
    from patchright.sync_api import sync_playwright

    logger = get_logger(__name__)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PatchrightError as exc:
            raise AntibotError(
                f"patchright failed to launch chromium for {url} "
                "(run: patchright install chromium)"
            ) from exc

        try:
            page = browser.new_page()
            try:
                try:
                    response = page.goto(url, timeout=timeout_ms)
                    if click_selector:
                        page.click(click_selector, timeout=timeout_ms)
                    # The same capability CamoufoxProvider's own
                    # solve function relies on: hold the browser open
                    # past `load` so async, post-load challenge JS gets a
                    # real chance to finish, instead of tearing the
                    # browser down the instant `load` fires
                    # (ByparrProvider's own structural constraint) -- or,
                    # if a click just happened above, give whatever it
                    # triggered time to settle before reading content.
                    page.wait_for_timeout(post_load_wait_ms)
                    html = page.content()
                    status = response.status if response is not None else 200
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    # Same reasoning as camoufox_provider.py's identical
                    # log line: status 200 alone means nothing for a
                    # provider that solves anti-bot challenges (a
                    # challenge/deny page is routinely served as a normal
                    # 200) -- this is the real evidence of whether content
                    # actually got past the challenge.
                    logger.info(
                        "patchright_provider.solved",
                        extra={
                            "url": url,
                            "final_url": page.url,
                            "status": status,
                            "title": page.title(),
                            "html_length": len(html),
                            "cookie_names": sorted(cookies),
                            "click_selector": click_selector,
                        },
                    )
                    return _RawSolve(url=page.url, html=html, status=status, cookies=cookies)
                except PatchrightError as exc:
                    raise AntibotError(f"patchright failed to solve {url}: {exc}") from exc
            finally:
                page.close()
        finally:
            browser.close()


class PatchrightProvider(AntibotProvider):
    """Solves anti-bot challenges by driving a real Patchright-stealthed
    Chromium directly -- the "lighter than Camoufox" option."""

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        post_load_wait_ms: int = DEFAULT_POST_LOAD_WAIT_MS,
        solve_fn: PatchrightSolveFn | None = None,
        logger: Logger | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise AntibotError(f"timeout_ms must be > 0, got {timeout_ms}")
        if post_load_wait_ms < 0:
            raise AntibotError(f"post_load_wait_ms must be >= 0, got {post_load_wait_ms}")
        self._timeout_ms = timeout_ms
        self._post_load_wait_ms = post_load_wait_ms
        self._solve_fn = solve_fn or _default_patchright_solve
        self.logger = logger or get_logger(__name__)

    def solve(self, url: str, click_selector: str | None = None) -> Solution:
        try:
            raw = self._solve_fn(
                url, self._timeout_ms, self._post_load_wait_ms, click_selector
            )
        except AntibotError:
            self.logger.error("patchright_provider.solve_failed", extra={"url": url})
            raise

        return Solution(
            url=raw.url,
            html=raw.html,
            status_code=raw.status,
            cookies=raw.cookies,
            solved_at=datetime.now(tz=UTC),
        )
