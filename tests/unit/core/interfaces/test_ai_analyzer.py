"""Unit tests for src/core/interfaces/ai_analyzer.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.interfaces.ai_analyzer import AIAnalyzer, AnalysisResult


class _FakeAIAnalyzer(AIAnalyzer):
    """Minimal concrete implementation used only to exercise the contract."""

    def analyze(self, text: str) -> AnalysisResult:
        return AnalysisResult(summary=text[:10], entities=["x"], confidence=0.5)


def test_concrete_implementation_satisfies_the_contract() -> None:
    """Happy path: a full implementation instantiates and returns a result."""
    analyzer: AIAnalyzer = _FakeAIAnalyzer()
    result = analyzer.analyze("some input text")
    assert isinstance(result, AnalysisResult)
    assert result.confidence == 0.5


def test_abstract_class_cannot_be_instantiated_directly() -> None:
    """Failure case 1: the ABC itself is not instantiable."""
    with pytest.raises(TypeError):
        AIAnalyzer()  # type: ignore[abstract]


def test_analysis_result_rejects_confidence_out_of_range() -> None:
    """Failure case 2: confidence must be within [0, 1]."""
    with pytest.raises(ValidationError):
        AnalysisResult(summary="s", entities=[], confidence=1.5)
