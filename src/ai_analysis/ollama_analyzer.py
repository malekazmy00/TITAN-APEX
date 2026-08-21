"""Ollama implementation of the :class:`AIAnalyzer` interface.

Talks to a local/remote Ollama instance (``/api/generate``) over plain
HTTP — no model-loading or GPU code lives here. Output is forced into the
exact shape of :class:`AnalysisResult` via Ollama's ``format`` parameter
(a JSON schema): the model is constrained to emit valid, schema-matching
JSON, never free text, so ``analyze()`` never has to guess how to parse a
prose response.

Default model: **qwen3:14b** — a general-purpose instruction model, not
``qwen2.5-coder``, since this analyzer summarizes/classifies scraped OSINT
text rather than reasoning about code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from logging import Logger
from typing import Any

from pydantic import ValidationError

from src.core.exceptions import AIAnalyzerError
from src.core.interfaces.ai_analyzer import AIAnalyzer, AnalysisResult
from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 120_000  # local LLM inference can be slow, especially on CPU
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:14b"

HttpPost = Callable[[str, dict[str, Any], int], str]

_SYSTEM_PROMPT = (
    "You are an OSINT analysis assistant. Given a piece of scraped text, "
    "produce a concise summary, a list of notable named entities (people, "
    "organizations, locations, products), and a confidence score between "
    "0 and 1 for your own analysis. Respond with JSON only, matching the "
    "provided schema exactly."
)

# Forces Ollama's structured-output mode: the model is constrained to emit
# JSON matching this schema, not free text.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "entities": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "entities", "confidence"],
}


def _default_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    # `with` guarantees the connection is closed even if reading the body fails.
    with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:  # noqa: S310
        return str(response.read().decode("utf-8"))


class OllamaAnalyzer(AIAnalyzer):
    """Analyzes text via Ollama, forcing structured JSON output."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        http_post: HttpPost | None = None,
        logger: Logger | None = None,
    ) -> None:
        if not base_url:
            raise AIAnalyzerError("ollama analyzer requires a non-empty base_url")
        if not model:
            raise AIAnalyzerError("ollama analyzer requires a non-empty model name")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_ms = timeout_ms
        self._http_post = http_post or _default_http_post
        self.logger = logger or get_logger(__name__)

    def analyze(self, text: str) -> AnalysisResult:
        payload = {
            "model": self._model,
            "system": _SYSTEM_PROMPT,
            "prompt": text,
            "format": _RESPONSE_SCHEMA,
            "stream": False,
        }

        try:
            raw_response = self._http_post(
                f"{self._base_url}/api/generate", payload, self._timeout_ms
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            self.logger.error("ollama_analyzer.request_failed", extra={"reason": str(exc)})
            raise AIAnalyzerError(f"ollama request failed: {exc}") from exc

        try:
            envelope = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            self.logger.error("ollama_analyzer.invalid_envelope_json")
            raise AIAnalyzerError("ollama returned invalid JSON envelope") from exc

        if not isinstance(envelope, dict) or "response" not in envelope:
            self.logger.error("ollama_analyzer.malformed_envelope")
            raise AIAnalyzerError("ollama response envelope is missing the 'response' field")

        try:
            structured = json.loads(envelope["response"])
        except (json.JSONDecodeError, TypeError) as exc:
            self.logger.error("ollama_analyzer.invalid_structured_output")
            raise AIAnalyzerError("ollama's structured output was not valid JSON") from exc

        try:
            return AnalysisResult.model_validate(structured)
        except ValidationError as exc:
            self.logger.error("ollama_analyzer.schema_validation_failed")
            raise AIAnalyzerError(f"ollama output failed schema validation: {exc}") from exc


def analyzer_from_env() -> OllamaAnalyzer:
    """Build an :class:`OllamaAnalyzer` from ``TITAN_OLLAMA_URL`` / ``TITAN_AI_MODEL``.

    Both fall back to sane defaults (a local Ollama, ``qwen3:14b``) — see
    ``.env.example``. Nothing here is hardcoded for a specific deployment.
    """
    base_url = os.environ.get("TITAN_OLLAMA_URL", DEFAULT_OLLAMA_URL)
    model = os.environ.get("TITAN_AI_MODEL", DEFAULT_MODEL)
    return OllamaAnalyzer(base_url=base_url, model=model)
