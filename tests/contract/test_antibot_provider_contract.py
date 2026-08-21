"""Contract test suite for AntibotProvider implementations.

Every provider implementing the ``AntibotProvider`` interface must pass
this suite before it is accepted into the project (docs/REQUIREMENTS.md,
sections 1 & 4). Parametrized across every implementation this project
has (docs/REQUIREMENTS.md section 9 entry 4 / round 3 added
``CamoufoxProvider`` alongside the original ``ByparrProvider``; this
phase's revision added ``PatchrightProvider`` as a third, lighter-weight
option) — a future provider must pass the same suite too, by adding one
more entry to ``_PROVIDERS`` below.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider, Solution
from src.providers.antibot.byparr_provider import ByparrProvider
from src.providers.antibot.camoufox_provider import CamoufoxProvider
from src.providers.antibot.camoufox_provider import _RawSolve as _CamoufoxRawSolve
from src.providers.antibot.patchright_provider import PatchrightProvider
from src.providers.antibot.patchright_provider import _RawSolve as _PatchrightRawSolve

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


def _byparr_ok_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return _SOLVED_RESPONSE


def _byparr_failing_transport(url: str, payload: dict[str, Any], timeout_ms: int) -> str:
    return json.dumps({"status": "error", "message": "unsolvable"})


def _build_byparr_ok() -> AntibotProvider:
    return ByparrProvider(base_url="http://localhost:8191", http_post=_byparr_ok_transport)


def _build_byparr_failing() -> AntibotProvider:
    return ByparrProvider(base_url="http://localhost:8191", http_post=_byparr_failing_transport)


def _camoufox_ok_solve(
    url: str, timeout_ms: int, post_load_wait_ms: int, click_selector: str | None = None
) -> _CamoufoxRawSolve:
    return _CamoufoxRawSolve(
        url="https://example.com/protected",
        html="<html>past the challenge</html>",
        status=200,
        cookies={"cf_clearance": "token"},
    )


def _camoufox_failing_solve(
    url: str, timeout_ms: int, post_load_wait_ms: int, click_selector: str | None = None
) -> _CamoufoxRawSolve:
    raise AntibotError(f"camoufox failed to solve {url}: unsolvable")


def _build_camoufox_ok() -> AntibotProvider:
    return CamoufoxProvider(solve_fn=_camoufox_ok_solve)


def _build_camoufox_failing() -> AntibotProvider:
    return CamoufoxProvider(solve_fn=_camoufox_failing_solve)


def _patchright_ok_solve(
    url: str, timeout_ms: int, post_load_wait_ms: int, click_selector: str | None = None
) -> _PatchrightRawSolve:
    return _PatchrightRawSolve(
        url="https://example.com/protected",
        html="<html>past the challenge</html>",
        status=200,
        cookies={"cf_clearance": "token"},
    )


def _patchright_failing_solve(
    url: str, timeout_ms: int, post_load_wait_ms: int, click_selector: str | None = None
) -> _PatchrightRawSolve:
    raise AntibotError(f"patchright failed to solve {url}: unsolvable")


def _build_patchright_ok() -> AntibotProvider:
    return PatchrightProvider(solve_fn=_patchright_ok_solve)


def _build_patchright_failing() -> AntibotProvider:
    return PatchrightProvider(solve_fn=_patchright_failing_solve)


_PROVIDERS = [
    (_build_byparr_ok, _build_byparr_failing),
    (_build_camoufox_ok, _build_camoufox_failing),
    (_build_patchright_ok, _build_patchright_failing),
]
_PROVIDER_IDS = ["byparr", "camoufox", "patchright"]


@pytest.fixture(params=_PROVIDERS, ids=_PROVIDER_IDS)
def provider_factories(request: pytest.FixtureRequest) -> Any:
    return request.param


@pytest.fixture
def provider(provider_factories: Any) -> AntibotProvider:
    ok_factory, _failing_factory = provider_factories
    return ok_factory()  # type: ignore[no-any-return]


def test_is_an_antibot_provider(provider: AntibotProvider) -> None:
    assert isinstance(provider, AntibotProvider)


def test_solve_returns_a_solution(provider: AntibotProvider) -> None:
    solution = provider.solve("https://example.com/protected")

    assert isinstance(solution, Solution)
    assert solution.status_code == 200
    assert solution.html
    assert solution.cookies


def test_solve_raises_antibot_error_on_failure(provider_factories: Any) -> None:
    _ok_factory, failing_factory = provider_factories
    failing_provider: AntibotProvider = failing_factory()

    with pytest.raises(AntibotError):
        failing_provider.solve("https://example.com/protected")


def test_solve_accepts_an_optional_click_selector_without_crashing(
    provider: AntibotProvider,
) -> None:
    """click_selector (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md,
    cookie-consent-wall round) is best-effort, not part of the required
    contract (AntibotProvider.solve's own docstring) -- every provider
    must still accept the keyword and return a Solution without crashing,
    even one (ByparrProvider) that cannot actually act on it and only logs
    a warning instead."""
    solution = provider.solve("https://example.com/protected", click_selector="#accept-cookies")

    assert isinstance(solution, Solution)
