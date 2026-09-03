"""Integration test: Layer 3 ("Strategy Engine") real-time wiring,
proven against real HTTP responses from the live test-environment/
stack -- test-environment/mock-target/app.py's own ``/reject-pattern``
route (docs/REQUIREMENTS.md section 9 entry 30), the exact same
fixtures ``test_response_classifier_live.py`` (entry 29) already
introduced.

Same deliberate, documented shape as that file: direct
``CircuitBreakerMiddleware.process_response()`` calls against a real
``scrapy.http.Response`` built from a real ``urllib.request`` fetch, not
a full ``scrapy runspider`` crawl -- for the identical reason (a full
crawl would let ``SWITCH_PROVIDER``'s enacted retry actually re-enter
the engine and trigger one real Byparr solve attempt against a static
page with nothing to solve). See that file's own module docstring for
the complete reasoning; not repeated here.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack -- same gate as
test_response_classifier_live.py and every other mock-target live test.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from scrapy.http import Request, Response

from src.middlewares.circuit_breaker import CircuitBreakerMiddleware
from src.strategy.strategy_capability import StrategyEngineConfig, StrategyMode
from src.strategy.strategy_engine import StrategyEngine
from src.strategy.strategy_registry import PATH_ENV_VAR

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
MOCK_TARGET_BASE_URL = "http://localhost:8080"


def _fetch(pattern: str) -> tuple[int, dict[str, str], bytes]:
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern={pattern}"
    request = urllib.request.Request(url)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_adjust_backoff_scales_the_cooldown_against_a_real_silent_block_response(
    tmp_path: Path,
) -> None:
    """StrategyCapability.ADJUST_BACKOFF, ENACT mode, against a real
    silent-block 403 (test_response_classifier_live.py's own
    test_classify_response_matches_the_real_mock_target_shape already
    proves this classifies correctly -- this test is about the Layer 3
    dispatch on top of that real classification)."""
    status, headers, body = _fetch("empty")
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern=empty"
    request = Request(url, meta={"strategy_backoff_multiplier": 2.0})
    response = Response(url=url, status=status, headers=headers, body=body, request=request)

    decision_log_path = tmp_path / "strategy_decisions.jsonl"
    os.environ[PATH_ENV_VAR] = str(decision_log_path)
    try:
        engine = StrategyEngine(
            StrategyEngineConfig(engine_enabled=True, adjust_backoff_mode=StrategyMode.ENACT)
        )
        middleware = CircuitBreakerMiddleware(
            silent_block_cooldown_seconds=300.0, strategy_engine=engine
        )
        result = middleware.process_response(request, response, spider=object())
    finally:
        os.environ.pop(PATH_ENV_VAR, None)

    assert isinstance(result, Response)
    circuit = middleware._circuits["localhost:8080"]
    assert circuit.cooldown_override == 600.0  # 300.0 * 2.0

    assert decision_log_path.exists()
    records = [
        json.loads(line) for line in decision_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["capability"] == "adjust-backoff"
    assert records[0]["enacted"] is True
    assert records[0]["proposed_action"]["adjusted_cooldown_seconds"] == 600.0


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_switch_provider_rotates_the_retry_requests_provider_against_a_real_challenge_page(
    tmp_path: Path,
) -> None:
    """StrategyCapability.SWITCH_PROVIDER, ENACT mode, against a real
    challenge-page 403 -- same "prove the dispatch, not the antibot
    escalation itself" scope as test_response_classifier_live.py's own
    TRY_ANTIBOT_PROVIDER test."""
    status, headers, body = _fetch("challenge")
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern=challenge"
    request = Request(url, meta={"antibot_provider": "byparr"})
    response = Response(url=url, status=status, headers=headers, body=body, request=request)

    decision_log_path = tmp_path / "strategy_decisions.jsonl"
    os.environ[PATH_ENV_VAR] = str(decision_log_path)
    try:
        engine = StrategyEngine(
            StrategyEngineConfig(
                engine_enabled=True,
                switch_provider_mode=StrategyMode.ENACT,
                switch_provider_after_n_challenges=1,
            )
        )
        middleware = CircuitBreakerMiddleware(strategy_engine=engine)
        result = middleware.process_response(request, response, spider=object())
    finally:
        os.environ.pop(PATH_ENV_VAR, None)

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "camoufox"

    records = [
        json.loads(line) for line in decision_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["capability"] == "switch-provider"
    assert records[0]["enacted"] is True
    assert records[0]["proposed_action"]["new_provider"] == "camoufox"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_disabled_engine_leaves_a_real_classified_rejection_completely_unchanged() -> None:
    """Regression proof against real data: the default, master-switch-off
    engine changes nothing about Layer 2's own real, already-CI-confirmed
    behavior (entry 29) -- even against the exact same live challenge-page
    response SWITCH_PROVIDER's own test above enacts a change against."""
    status, headers, body = _fetch("challenge")
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern=challenge"
    request = Request(url, meta={"antibot_provider": "byparr"})
    response = Response(url=url, status=status, headers=headers, body=body, request=request)

    middleware = CircuitBreakerMiddleware()  # default StrategyEngine: disabled
    result = middleware.process_response(request, response, spider=object())

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "byparr"
