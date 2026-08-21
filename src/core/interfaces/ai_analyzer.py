"""Contract for AI analysis providers (e.g. Qwen via Ollama, Phase 5).

This module defines the abstract contract only. No concrete implementation
lives here yet — see ``src/ai_analysis/`` (Phase 5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class AnalysisResult(BaseModel):
    """Result of running an AI analysis pass over a piece of text."""

    summary: str
    entities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class AIAnalyzer(ABC):
    """Abstract contract every AI analysis provider implementation must follow.

    Implementations must raise
    :class:`src.core.exceptions.AIAnalyzerError` (never a bare
    ``Exception``) on failure, and must release any external resource
    (model handle, HTTP connection, ...) in a ``finally`` block.
    """

    @abstractmethod
    def analyze(self, text: str) -> AnalysisResult:
        """Analyze ``text`` and return a structured :class:`AnalysisResult`.

        Raises:
            src.core.exceptions.AIAnalyzerError: if analysis fails (e.g.
                model unavailable, malformed model response).
        """
        raise NotImplementedError
