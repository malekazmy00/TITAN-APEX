"""Camoufox implementation of the :class:`AntibotProvider` interface.

Camoufox (https://github.com/daijro/camoufox, MPL-2.0) is a real,
Firefox-based stealth browser with a Playwright-compatible Python API
(``camoufox.sync_api.Camoufox`` subclasses Playwright's own
``PlaywrightContextManager`` -- ``browser.new_page()``/``page.goto()``
return genuine Playwright objects).

Unlike :class:`~src.providers.antibot.byparr_provider.ByparrProvider`
(which delegates to Byparr's own HTTP API and has no control over when
Byparr's browser closes), this provider drives the browser itself,
in-process -- giving it direct control over the browser's lifecycle. That
is the whole point of it existing: docs/REQUIREMENTS.md section 9 entry 4
found, by direct observation, that Byparr's ``/v1`` API tears its browser
down as soon as the page's ``load`` event fires, before a challenge that
does real work *asynchronously after* ``load`` (like Anubis's real
proof-of-work flow) ever gets a chance to finish. This provider adds an
explicit, configurable wait *after* ``load`` before reading the page and
closing the browser -- the same ``render_wait_ms`` idea already proven
for :func:`~src.middlewares.playwright_middleware.render_with_playwright`,
applied here to the antibot-solving path instead of the render path.

The actual browser-driving call is injectable (``solve_fn``) so unit
tests never launch a real browser or touch the network.
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
# How long to wait after the page's `load` event before reading content
# and closing the browser -- long enough for a fast (difficulty-2, per
# Anubis's own shipped thresholds) proof-of-work challenge to compute and
# round-trip, confirmed sufficient by hand against the real
# test-environment/ stack (docs/REQUIREMENTS.md section 9 entry 4/round 3).
DEFAULT_POST_LOAD_WAIT_MS = 5_000


class _RawSolve(NamedTuple):
    """What a real browser-driving call actually produces."""

    url: str
    html: str
    status: int
    cookies: dict[str, str]


# (url, timeout_ms, post_load_wait_ms) -> raw browser result
CamoufoxSolveFn = Callable[[str, int, int], _RawSolve]


def _default_camoufox_solve(url: str, timeout_ms: int, post_load_wait_ms: int) -> _RawSolve:
    """Drive a real Camoufox browser: navigate, wait past ``load``, read, close.

    Raises:
        AntibotError: if the browser fails to launch, navigate, or read
            the page -- wraps Camoufox's own pre-launch exceptions and
            Playwright's own navigation/page errors (Camoufox's
            ``new_page()``/``goto()``/``content()`` are genuine Playwright
            calls under the hood).
    """
    from camoufox.exceptions import CamoufoxNotInstalled
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import Error as PlaywrightError

    try:
        # camoufox ships no py.typed marker / inline stubs, so mypy sees
        # this constructor call itself (not objects it returns -- those
        # are genuine Playwright objects, typed as Any below the same
        # way playwright_middleware.py already treats them) as untyped.
        with Camoufox(headless=True) as browser:  # type: ignore[no-untyped-call]
            try:
                page = browser.new_page()
                try:
                    response = page.goto(url, timeout=timeout_ms)
                    # The one thing ByparrProvider structurally cannot
                    # do: hold the browser open past `load` so async,
                    # post-load challenge JS (e.g. Anubis's real PoW
                    # flow) gets a real chance to finish.
                    page.wait_for_timeout(post_load_wait_ms)
                    html = page.content()
                    status = response.status if response is not None else 200
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    return _RawSolve(url=page.url, html=html, status=status, cookies=cookies)
                finally:
                    page.close()
            finally:
                browser.close()
    except CamoufoxNotInstalled as exc:
        raise AntibotError(
            f"camoufox browser binary not installed for {url} "
            "(run: python -m camoufox fetch)"
        ) from exc
    except PlaywrightError as exc:
        raise AntibotError(f"camoufox failed to solve {url}: {exc}") from exc


class CamoufoxProvider(AntibotProvider):
    """Solves anti-bot challenges by driving a real Camoufox browser directly."""

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        post_load_wait_ms: int = DEFAULT_POST_LOAD_WAIT_MS,
        solve_fn: CamoufoxSolveFn | None = None,
        logger: Logger | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise AntibotError(f"timeout_ms must be > 0, got {timeout_ms}")
        if post_load_wait_ms < 0:
            raise AntibotError(f"post_load_wait_ms must be >= 0, got {post_load_wait_ms}")
        self._timeout_ms = timeout_ms
        self._post_load_wait_ms = post_load_wait_ms
        self._solve_fn = solve_fn or _default_camoufox_solve
        self.logger = logger or get_logger(__name__)

    def solve(self, url: str) -> Solution:
        try:
            raw = self._solve_fn(url, self._timeout_ms, self._post_load_wait_ms)
        except AntibotError:
            self.logger.error("camoufox_provider.solve_failed", extra={"url": url})
            raise

        return Solution(
            url=raw.url,
            html=raw.html,
            status_code=raw.status,
            cookies=raw.cookies,
            solved_at=datetime.now(tz=UTC),
        )
