"""Contract for anti-bot solving providers (e.g. Byparr, a future service).

This module defines the abstract contract only. No concrete implementation
lives here — see ``src/providers/antibot/`` for implementations (Phase 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field


class Solution(BaseModel):
    """Result of successfully solving an anti-bot challenge for a URL."""

    url: str
    html: str
    status_code: int
    cookies: dict[str, str] = Field(default_factory=dict)
    solved_at: datetime


class AntibotProvider(ABC):
    """Abstract contract every anti-bot provider implementation must follow.

    Implementations are swappable: the rest of the codebase depends only on
    this interface, never on a concrete provider (docs/REQUIREMENTS.md,
    section 1 — "مبدأ التوسع الأساسي").
    """

    @abstractmethod
    def solve(self, url: str, click_selector: str | None = None) -> Solution:
        """Solve whatever anti-bot challenge protects ``url``.

        ``click_selector`` (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md,
        cookie-consent-wall round): an optional CSS selector for an
        element to click after the page loads -- e.g. a cookie-consent
        "Accept" button/link that gates real content -- before reading the
        page and returning a :class:`Solution`. This is **best-effort, not
        part of the required contract**: a provider that drives a real
        browser (in-process) can click; a provider that only delegates to
        an external HTTP-only solving service structurally may not be able
        to. A provider that cannot support it must not crash or silently
        drop it -- it must log a clear warning identifying exactly what
        was skipped and why, then proceed to solve without clicking, so
        the gap is visible in evidence rather than hidden.

        Implementations must raise
        :class:`src.core.exceptions.AntibotError` (never a bare
        ``Exception``) on failure, and must release any external resource
        they acquire (browser handle, HTTP connection, ...) in a
        ``finally`` block even when solving fails.
        """
        raise NotImplementedError
