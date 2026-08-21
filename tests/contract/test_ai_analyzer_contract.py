"""Contract test suite for AIAnalyzer implementations.

Every provider implementing the ``AIAnalyzer`` interface must pass this
suite before it is accepted into the project (docs/REQUIREMENTS.md,
sections 1 & 4). Currently exercised against ``OllamaAnalyzer`` with an
injected HTTP transport (no real network, no GPU) — a future provider
(a different model, a different local runtime, ...) must pass the same
suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.ai_analysis.ollama_analyzer import OllamaAnalyzer
from src.core.exceptions import AIAnalyzerError
from src.core.interfaces.ai_analyzer import AIAnalyzer, AnalysisResult

_STRUCTURED = {"summary": "concise summary", "entities": ["Acme Corp"], "confidence": 0.75}


def _ok_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return json.dumps({"model": "qwen3:14b", "response": json.dumps(_STRUCTURED)})


def _failing_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return json.dumps({"model": "qwen3:14b"})  # missing "response"


@pytest.fixture
def analyzer() -> AIAnalyzer:
    return OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=_ok_transport
    )


def test_is_an_ai_analyzer(analyzer: AIAnalyzer) -> None:
    assert isinstance(analyzer, AIAnalyzer)


def test_analyze_returns_an_analysis_result(analyzer: AIAnalyzer) -> None:
    result = analyzer.analyze("some scraped text")

    assert isinstance(result, AnalysisResult)
    assert result.summary
    assert 0.0 <= result.confidence <= 1.0


def test_analyze_raises_ai_analyzer_error_on_failure() -> None:
    failing_analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=_failing_transport
    )

    with pytest.raises(AIAnalyzerError):
        failing_analyzer.analyze("some scraped text")
