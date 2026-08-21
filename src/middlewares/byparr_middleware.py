"""Downloader middleware: solves anti-bot-protected pages via Byparr.

Only requests explicitly marked ``request.meta["antibot_needed"] = True``
are routed through the configured :class:`AntibotProvider` (set by
``GenericSpider`` when a target's config sets ``antibot_needed: true``) —
every other request passes straight through untouched.

Cookie management is automatic: the solved session's cookies are attached
to the response as ``Set-Cookie`` headers, so Scrapy's own
``CookiesMiddleware`` (already in the default chain) picks them up and
carries them on every later request to that domain — no custom cookie jar
needed here.

Fallback is explicit and logged, never a crash: if no provider is
configured, or the provider fails, the request is handed back to the
normal downloader (``process_request`` returns ``None``) instead of
dropping the request or raising.
"""

from __future__ import annotations

from logging import Logger
from typing import Any

from scrapy.http import HtmlResponse, Request, Response
from scrapy.http.headers import Headers

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider
from src.logging_config import get_logger
from src.providers.antibot.byparr_provider import ByparrProvider


class ByparrMiddleware:
    """Renders ``request.meta["antibot_needed"]``-flagged requests via Byparr."""

    def __init__(
        self, provider: AntibotProvider | None = None, logger: Logger | None = None
    ) -> None:
        self.provider = provider
        self.logger = logger or get_logger(__name__)

    @classmethod
    def from_crawler(cls, crawler: Any) -> ByparrMiddleware:
        base_url = crawler.settings.get("TITAN_BYPARR_URL")
        provider = ByparrProvider(base_url=base_url) if base_url else None
        return cls(provider=provider)

    def process_request(self, request: Request, spider: Any) -> Response | None:
        if not request.meta.get("antibot_needed"):
            return None

        if self.provider is None:
            self.logger.warning(
                "byparr_middleware.not_configured_fallback",
                extra={"url": request.url, "hint": "set TITAN_BYPARR_URL to enable Byparr"},
            )
            return None

        try:
            solution = self.provider.solve(request.url)
        except AntibotError as exc:
            self.logger.error(
                "byparr_middleware.solve_failed_fallback",
                extra={"url": request.url, "reason": str(exc)},
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
