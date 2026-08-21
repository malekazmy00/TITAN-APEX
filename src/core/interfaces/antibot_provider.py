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
    def solve(self, url: str) -> Solution:
        """Solve whatever anti-bot challenge protects ``url``.

        Implementations must raise
        :class:`src.core.exceptions.AntibotError` (never a bare
        ``Exception``) on failure, and must release any external resource
        they acquire (browser handle, HTTP connection, ...) in a
        ``finally`` block even when solving fails.
        """
        raise NotImplementedError
