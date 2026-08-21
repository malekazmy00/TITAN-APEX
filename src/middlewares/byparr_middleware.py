"""Downloader middleware: solves anti-bot-protected pages via a configured
:class:`AntibotProvider`.

Only requests explicitly marked ``request.meta["antibot_needed"] = True``
are routed this way (set by ``GenericSpider`` when a target's config sets
``antibot_needed: true``) — every other request passes straight through
untouched.

Which provider handles the request is itself config-driven: SpiderConfig's
``antibot_provider`` field (default ``"byparr"``, or ``"camoufox"`` /
``"patchright"``) reaches here as ``request.meta["antibot_provider"]``. Still named
``ByparrMiddleware`` — Byparr was the original and, until
docs/REQUIREMENTS.md section 9 entry 4 (round 3), only provider this
project had — but it now dispatches across whichever ``AntibotProvider``
implementation a target selects, never target-specific code (section 1's
"مبدأ التوسع الأساسي"): a new provider is a new implementation of the
same interface, plumbed in here once, not new code anywhere that depends
on it.

Provider solving runs off the Twisted reactor thread (``deferToThread``),
the same way ``PlaywrightMiddleware`` already has to for its own renderer
— not an optional nicety here: confirmed for real
(docs/REQUIREMENTS.md section 9 entry 5) that ``CamoufoxProvider``, which
drives a real Playwright-based browser directly, crashes outright
("Playwright Sync API inside the asyncio loop") when called straight from
``process_request`` on Scrapy's own asyncio-reactor thread. ByparrProvider
(plain blocking HTTP) never technically needed the thread hop, but doesn't
mind it either — this also stops a slow Byparr solve from blocking the
reactor thread, a genuine (if secondary) improvement.

Cookie management is automatic: the solved session's cookies are attached
to the response as ``Set-Cookie`` headers, so Scrapy's own
``CookiesMiddleware`` (already in the default chain) picks them up and
carries them on every later request to that domain — no custom cookie jar
needed here.

Fallback is explicit and logged, never a crash: if the selected provider
isn't configured, or it fails, the request is handed back to the normal
downloader (the resolved response is ``None``) instead of dropping the
request or raising.

``request.meta["click_selector"]`` (already populated by ``GenericSpider``
for every request, docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
cookie-consent-wall round) is passed straight through to whichever
provider is selected -- best-effort per
:meth:`~src.core.interfaces.antibot_provider.AntibotProvider.solve`'s own
contract: a real-browser-driving provider (Camoufox, Patchright) can
click; ByparrProvider cannot and only logs why.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from logging import Logger
from typing import Any

from scrapy.http import HtmlResponse, Request, Response
from scrapy.http.headers import Headers
from twisted.internet.defer import Deferred
from twisted.internet.threads import deferToThread

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider
from src.logging_config import get_logger
from src.providers.antibot.byparr_provider import ByparrProvider
from src.providers.antibot.camoufox_provider import CamoufoxProvider
from src.providers.antibot.patchright_provider import PatchrightProvider

DEFAULT_PROVIDER_NAME = "byparr"

ThreadRunner = Callable[[Callable[[Request], "Response | None"], Request], Any]


def _default_thread_runner(
    solve_fn: Callable[[Request], Response | None], request: Request
) -> Deferred[Response | None]:
    return deferToThread(solve_fn, request)


class ByparrMiddleware:
    """Renders ``request.meta["antibot_needed"]``-flagged requests via
    whichever provider ``request.meta["antibot_provider"]`` selects."""

    def __init__(
        self,
        byparr_provider: AntibotProvider | None = None,
        camoufox_provider: AntibotProvider | None = None,
        patchright_provider: AntibotProvider | None = None,
        logger: Logger | None = None,
        thread_runner: ThreadRunner | None = None,
    ) -> None:
        self._providers: dict[str, AntibotProvider | None] = {
            "byparr": byparr_provider,
            "camoufox": camoufox_provider,
            "patchright": patchright_provider,
        }
        self.logger = logger or get_logger(__name__)
        self._thread_runner = thread_runner or _default_thread_runner

    @property
    def provider(self) -> AntibotProvider | None:
        """Backwards-compatible alias for the default ("byparr") provider."""
        return self._providers[DEFAULT_PROVIDER_NAME]

    @classmethod
    def from_crawler(cls, crawler: Any) -> ByparrMiddleware:
        # crawler.settings only auto-picks up OS environment variables
        # prefixed SCRAPY_ (Scrapy's own init_env()) -- TITAN_BYPARR_URL
        # never was one, so every previous check here (only ever exercised
        # via an explicit `-s TITAN_BYPARR_URL=...` in a test, since no
        # antibot_needed config existed until test-environment/'s
        # mock_target.yaml) was silently untested for the "just set the
        # env var, like every other TITAN_* setting" case. Falling back to
        # os.environ directly matches how every other TITAN_* setting
        # already works (src/settings.py) and is what actually running
        # `scrapy runspider` with only TITAN_BYPARR_URL set in the shell
        # (e.g. this project's own CI job-level env, or a real deploy)
        # requires to work at all.
        base_url = crawler.settings.get("TITAN_BYPARR_URL") or os.environ.get("TITAN_BYPARR_URL")
        byparr_provider = ByparrProvider(base_url=base_url) if base_url else None
        # Neither CamoufoxProvider nor PatchrightProvider needs an external
        # service/base_url -- both drive their own browser in-process -- so
        # both are always available, unlike Byparr which needs
        # TITAN_BYPARR_URL pointed at a running instance. Construction
        # itself is cheap for both (no browser launched until .solve()
        # actually runs).
        camoufox_provider: AntibotProvider = CamoufoxProvider()
        patchright_provider: AntibotProvider = PatchrightProvider()
        return cls(
            byparr_provider=byparr_provider,
            camoufox_provider=camoufox_provider,
            patchright_provider=patchright_provider,
        )

    def process_request(self, request: Request, spider: Any) -> Any:
        if not request.meta.get("antibot_needed"):
            return None
        return self._thread_runner(self._solve, request)

    def _solve(self, request: Request) -> Response | None:
        provider_name = request.meta.get("antibot_provider") or DEFAULT_PROVIDER_NAME
        provider = self._providers.get(provider_name)
        if provider is None:
            self.logger.warning(
                "byparr_middleware.not_configured_fallback",
                extra={
                    "url": request.url,
                    "provider": provider_name,
                    "hint": "set TITAN_BYPARR_URL to enable Byparr"
                    if provider_name == "byparr"
                    else f"unknown antibot_provider: {provider_name!r}",
                },
            )
            return None

        click_selector = request.meta.get("click_selector")
        try:
            solution = provider.solve(request.url, click_selector=click_selector)
        except AntibotError as exc:
            self.logger.error(
                "byparr_middleware.solve_failed_fallback",
                extra={"url": request.url, "provider": provider_name, "reason": str(exc)},
            )
            return None  # Fallback: let the normal downloader try instead of crashing.

        headers = Headers()
        for name, value in solution.cookies.items():
            headers.appendlist("Set-Cookie", f"{name}={value}")

        return HtmlResponse(
            url=solution.url,
            body=solution.html.encode("utf-8"),
            status=solution.status_code,
            headers=headers,
            request=request,
        )
