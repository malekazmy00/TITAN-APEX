"""Repeated-failure alerting.

Fired when a middleware detects that a failure threshold has been
crossed — today, only ``CircuitBreakerMiddleware`` opening a circuit
(docs/REQUIREMENTS.md, Phase 4: "فشل متكرر = تنبيه"). Always logs at
CRITICAL. Additionally POSTs a JSON payload to a webhook if
``TITAN_ALERT_WEBHOOK_URL`` is configured — delivery failure is caught
and logged, never allowed to crash the crawl that triggered the alert in
the first place.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from logging import Logger
from typing import Any

from pydantic import BaseModel

from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 5_000

HttpPost = Callable[[str, dict[str, Any], int], None]


class AlertEvent(BaseModel):
    """A single repeated-failure alert."""

    source: str
    domain: str
    reason: str
    consecutive_failures: int
    cooldown_seconds: float
    occurred_at: datetime


def _default_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    # `with` guarantees the connection is closed even if the POST fails.
    with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:  # noqa: S310
        response.read()


class AlertDispatcher:
    """Sends :class:`AlertEvent`\\ s: always logs, optionally POSTs to a webhook."""

    def __init__(
        self,
        webhook_url: str | None = None,
        http_post: HttpPost | None = None,
        logger: Logger | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        self._webhook_url = webhook_url
        self._http_post = http_post or _default_http_post
        self.logger = logger or get_logger(__name__)
        self._timeout_ms = timeout_ms

    def send(self, event: AlertEvent) -> None:
        self.logger.critical(
            "alert.repeated_failure",
            extra={
                "source": event.source,
                "domain": event.domain,
                "reason": event.reason,
                "consecutive_failures": event.consecutive_failures,
                "cooldown_seconds": event.cooldown_seconds,
            },
        )
        if not self._webhook_url:
            return

        try:
            self._http_post(
                self._webhook_url, json.loads(event.model_dump_json()), self._timeout_ms
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            self.logger.error(
                "alert.webhook_delivery_failed",
                extra={"webhook_url": self._webhook_url, "reason": str(exc)},
            )


def dispatcher_from_settings(settings: Any) -> AlertDispatcher:
    """Build an :class:`AlertDispatcher` from Scrapy ``crawler.settings``."""
    webhook_url = settings.get("TITAN_ALERT_WEBHOOK_URL") or None
    return AlertDispatcher(webhook_url=webhook_url)
