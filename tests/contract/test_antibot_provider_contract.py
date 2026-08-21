"""Contract test suite for AntibotProvider implementations.

Every provider implementing the ``AntibotProvider`` interface must pass
this suite before it is accepted into the project (docs/REQUIREMENTS.md,
sections 1 & 4). Currently exercised against ``ByparrProvider`` with an
injected HTTP transport (no real network) — a future provider must pass
the same suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider, Solution
from src.providers.antibot.byparr_provider import ByparrProvider

_SOLVED_RESPONSE = json.dumps(
    {
        "status": "ok",
        "solution": {
            "url": "https://example.com/protected",
            "status": 200,
            "response": "<html>past the challenge</html>",
            "cookies": [{"name": "cf_clearance", "value": "token"}],
        },
    }
)


def _ok_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return _SOLVED_RESPONSE


def _failing_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return json.dumps({"status": "error", "message": "unsolvable"})


@pytest.fixture
def provider() -> AntibotProvider:
    return ByparrProvider(base_url="http://localhost:8191", http_post=_ok_transport)


def test_is_an_antibot_provider(provider: AntibotProvider) -> None:
    assert isinstance(provider, AntibotProvider)


def test_solve_returns_a_solution(provider: AntibotProvider) -> None:
    solution = provider.solve("https://example.com/protected")

    assert isinstance(solution, Solution)
    assert solution.status_code == 200
    assert solution.html
    assert solution.cookies


def test_solve_raises_antibot_error_on_failure() -> None:
    failing_provider = ByparrProvider(
        base_url="http://localhost:8191", http_post=_failing_transport
    )

    with pytest.raises(AntibotError):
        failing_provider.solve("https://example.com/protected")
