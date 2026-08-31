"""Byparr implementation of the :class:`AntibotProvider` interface.

Byparr is a self-hosted, FlareSolverr-protocol-compatible anti-bot solving
service (Docker container, ``docker-compose.yml``): you POST a target URL
to it and it drives a real browser to solve whatever challenge (Cloudflare,
generic JS checks, ...) protects that page, returning the solved HTML and
the session cookies it collected.

This module only speaks that HTTP protocol — no browser automation lives
here. The HTTP transport is injectable so unit tests never touch a real
network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from typing import Any

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import (
    AntibotProvider,
    LiveDomSelectors,
    LoginFlow,
    Solution,
)
from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 60_000

# (base_url, endpoint_path, json_payload, timeout_ms) -> raw response body
HttpPost = Callable[[str, dict[str, Any], int], str]


def _default_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    # `with` guarantees the connection is closed even if reading the body fails.
    with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:  # noqa: S310
        return str(response.read().decode("utf-8"))


class ByparrProvider(AntibotProvider):
    """Solves anti-bot challenges by delegating to a Byparr HTTP service."""

    def __init__(
        self,
        base_url: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        http_post: HttpPost | None = None,
        logger: Logger | None = None,
    ) -> None:
        if not base_url:
            raise AntibotError("byparr provider requires a non-empty base_url")
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = timeout_ms
        self._http_post = http_post or _default_http_post
        self.logger = logger or get_logger(__name__)

    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
    ) -> Solution:
        if warm_session_urls:
            # Same structural gap as login_flow/progressive_extraction
            # below (docs/REQUIREMENTS.md section 9 entry 21, Step 2):
            # navigating through a real warm-up chain within one shared
            # browser session needs a live browser page -- Byparr's
            # /v1 protocol is a stateless "fetch and return HTML" HTTP
            # call with no page handle this process ever sees.
            self.logger.warning(
                "byparr_provider.warm_session_urls_unsupported",
                extra={
                    "url": url,
                    "reason": "byparr's /v1 API has no live page to walk a warm-up chain on",
                },
            )
        if use_accumulated_profile:
            # Same structural gap: an accumulated cookie/storage profile
            # needs a real browser context to load into and save from --
            # Byparr's /v1 protocol never hands this process a browser
            # context at all.
            self.logger.warning(
                "byparr_provider.use_accumulated_profile_unsupported",
                extra={
                    "url": url,
                    "reason": "byparr's /v1 API has no browser context to load/save a profile into",
                },
            )
        if login_flow is not None:
            # Same structural gap as extraction_selectors/click_selector
            # below (docs/REQUIREMENTS.md section 9 entry 15): filling
            # and submitting a real login form needs a live browser page
            # -- Byparr's /v1 protocol is a stateless "fetch and return
            # HTML" HTTP call with no page handle this process ever sees.
            # SpiderConfig's own validator (login requires antibot_provider
            # camoufox/patchright) should mean this branch is never
            # actually reached in practice -- logged and skipped anyway,
            # defense in depth.
            self.logger.warning(
                "byparr_provider.login_flow_unsupported",
                extra={
                    "url": url,
                    "reason": "byparr's /v1 API has no form-fill/interact capability at all",
                },
            )
        if progressive_extraction:
            # Same structural gap as extraction_selectors below (entry
            # 14, the real fix for entry 13's DOM Virtualization gap):
            # progressive collection needs a live browser page to scroll
            # and re-read step by step -- Byparr's /v1 protocol has no
            # such page. SpiderConfig's own validator should mean this
            # branch is never actually reached in practice -- logged and
            # skipped anyway, defense in depth.
            self.logger.warning(
                "byparr_provider.progressive_extraction_unsupported",
                extra={
                    "url": url,
                    "reason": "byparr's /v1 API returns HTML only, no live page to scroll",
                },
            )
        if extraction_selectors is not None:
            # Real, structural gap (docs/REQUIREMENTS.md section 9 entry
            # 12): live-DOM extraction needs a live browser page to query
            # -- Byparr's `/v1` protocol is a stateless "fetch and return
            # HTML" HTTP call with no page handle this process ever sees,
            # the same structural shape as click_selector's own gap right
            # below. SpiderConfig's own validator (extraction_mode:
            # "live_dom" requires antibot_provider camoufox/patchright)
            # should mean this branch is never actually reached in
            # practice -- logged and skipped anyway, not silently dropped
            # or crashed on, as defense in depth against that assumption
            # ever being bypassed.
            self.logger.warning(
                "byparr_provider.extraction_selectors_unsupported",
                extra={
                    "url": url,
                    "reason": "byparr's /v1 API returns HTML only, no live page to query",
                },
            )
        if click_selector:
            # Real, evidenced gap (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md,
            # cookie-consent-wall round): Byparr's `/v1` protocol
            # (its own README, checked) is a stateless "fetch and return"
            # call -- `cmd: request.get` has no click/interact parameter
            # at all, the same structural shape as the post-load-wait gap
            # (docs/REQUIREMENTS.md section 9 entry 4). Logged clearly and
            # skipped, not silently dropped or crashed on, so this gap
            # shows up in evidence instead of being hidden.
            self.logger.warning(
                "byparr_provider.click_selector_unsupported",
                extra={
                    "url": url,
                    "click_selector": click_selector,
                    "reason": "byparr's /v1 API has no click/interact parameter",
                },
            )
        payload = {"cmd": "request.get", "url": url, "maxTimeout": self._timeout_ms}

        try:
            raw_response = self._http_post(f"{self._base_url}/v1", payload, self._timeout_ms)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            self.logger.error(
                "byparr_provider.request_failed", extra={"url": url, "reason": str(exc)}
            )
            raise AntibotError(f"byparr request failed for {url}: {exc}") from exc

        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            self.logger.error("byparr_provider.invalid_json", extra={"url": url})
            raise AntibotError(f"byparr returned invalid JSON for {url}") from exc

        if not isinstance(data, dict) or data.get("status") != "ok":
            reason = data.get("message", "unknown error") if isinstance(data, dict) else data
            # "message" is a reserved LogRecord attribute name — extra={"message": ...}
            # raises KeyError at log time, so this uses "byparr_message" instead.
            self.logger.error(
                "byparr_provider.solve_failed", extra={"url": url, "byparr_message": str(reason)}
            )
            raise AntibotError(f"byparr failed to solve {url}: {reason}")

        try:
            solution_data = data["solution"]
            cookies = {
                cookie["name"]: cookie["value"] for cookie in solution_data.get("cookies", [])
            }
            return Solution(
                url=solution_data.get("url", url),
                html=solution_data["response"],
                status_code=solution_data.get("status", 200),
                cookies=cookies,
                solved_at=datetime.now(tz=UTC),
            )
        except (KeyError, TypeError) as exc:
            self.logger.error("byparr_provider.malformed_solution", extra={"url": url})
            raise AntibotError(f"byparr response for {url} is missing expected fields") from exc
