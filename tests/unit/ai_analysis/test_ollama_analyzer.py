"""Unit tests for src/ai_analysis/ollama_analyzer.py.

The HTTP transport is always injected: these tests never touch a real
network or a real Ollama instance.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from src.ai_analysis.ollama_analyzer import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    OllamaAnalyzer,
    analyzer_from_env,
)
from src.core.exceptions import AIAnalyzerError


def _envelope(structured: dict[str, Any]) -> str:
    return json.dumps({"model": "qwen3:14b", "response": json.dumps(structured), "done": True})


VALID_STRUCTURED = {
    "summary": "A hoodie is on sale for $52.",
    "entities": ["Chaz Kangeroo Hoodie"],
    "confidence": 0.87,
}


def test_analyze_returns_a_populated_analysis_result() -> None:
    """Happy path: a well-formed Ollama response yields a full AnalysisResult."""

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        assert payload["model"] == "qwen3:14b"
        assert payload["prompt"] == "some scraped text"
        assert payload["format"]["required"] == ["summary", "entities", "confidence"]
        assert payload["stream"] is False
        return _envelope(VALID_STRUCTURED)

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=fake_http_post
    )

    result = analyzer.analyze("some scraped text")

    assert result.summary == "A hoodie is on sale for $52."
    assert result.entities == ["Chaz Kangeroo Hoodie"]
    assert result.confidence == 0.87


def test_empty_base_url_raises_ai_analyzer_error() -> None:
    with pytest.raises(AIAnalyzerError, match="non-empty base_url"):
        OllamaAnalyzer(base_url="", model="qwen3:14b")


def test_empty_model_raises_ai_analyzer_error() -> None:
    with pytest.raises(AIAnalyzerError, match="non-empty model"):
        OllamaAnalyzer(base_url="http://localhost:11434", model="")


def test_connection_failure_raises_ai_analyzer_error() -> None:
    """Failure case 1: a transport-level connection error is wrapped, not raw."""

    def failing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        raise urllib.error.URLError("connection refused")

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=failing_http_post
    )

    with pytest.raises(AIAnalyzerError, match="ollama request failed"):
        analyzer.analyze("text")


def test_invalid_envelope_json_raises_ai_analyzer_error() -> None:
    """Failure case 2: a corrupted/non-JSON envelope is wrapped, not raw."""

    def bad_json_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return "{not valid json"

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=bad_json_http_post
    )

    with pytest.raises(AIAnalyzerError, match="invalid JSON envelope"):
        analyzer.analyze("text")


def test_missing_response_field_raises_ai_analyzer_error() -> None:
    """Failure case 3: an envelope missing 'response' is rejected, not silently None'd."""

    def missing_field_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"model": "qwen3:14b", "done": True})

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=missing_field_http_post
    )

    with pytest.raises(AIAnalyzerError, match="missing the 'response' field"):
        analyzer.analyze("text")


def test_non_json_structured_output_raises_ai_analyzer_error() -> None:
    """Failure case 4: the model's 'response' text isn't valid JSON."""

    def prose_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return json.dumps({"model": "qwen3:14b", "response": "sure, here's a summary: ..."})

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=prose_http_post
    )

    with pytest.raises(AIAnalyzerError, match="not valid JSON"):
        analyzer.analyze("text")


def test_structured_output_failing_schema_raises_ai_analyzer_error() -> None:
    """Failure case 5: a structurally-JSON response that violates AnalysisResult's
    own field constraints (confidence out of [0, 1]) is rejected, not silently clamped."""

    def out_of_range_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        return _envelope({"summary": "x", "entities": [], "confidence": 1.5})

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434", model="qwen3:14b", http_post=out_of_range_http_post
    )

    with pytest.raises(AIAnalyzerError, match="failed schema validation"):
        analyzer.analyze("text")


def test_base_url_trailing_slash_is_normalized() -> None:
    seen_urls: list[str] = []

    def capturing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
        seen_urls.append(url)
        return _envelope(VALID_STRUCTURED)

    analyzer = OllamaAnalyzer(
        base_url="http://localhost:11434/", model="qwen3:14b", http_post=capturing_http_post
    )
    analyzer.analyze("text")

    assert seen_urls == ["http://localhost:11434/api/generate"]


def test_analyzer_from_env_uses_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TITAN_OLLAMA_URL", raising=False)
    monkeypatch.delenv("TITAN_AI_MODEL", raising=False)

    analyzer = analyzer_from_env()

    assert analyzer._base_url == DEFAULT_OLLAMA_URL
    assert analyzer._model == DEFAULT_MODEL
    assert DEFAULT_MODEL == "qwen3:14b"


def test_analyzer_from_env_reads_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TITAN_OLLAMA_URL", "http://gpu-lab:11434")
    monkeypatch.setenv("TITAN_AI_MODEL", "qwen2.5-coder:14b")

    analyzer = analyzer_from_env()

    assert analyzer._base_url == "http://gpu-lab:11434"
    assert analyzer._model == "qwen2.5-coder:14b"
