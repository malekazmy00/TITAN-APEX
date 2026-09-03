"""Integration test: src/response_classifier.py's three ResponsePattern
values, proven against real HTTP responses from the live
test-environment/ stack (never a hand-built dict) --
test-environment/mock-target/app.py's own ``/reject-pattern`` route
(docs/REQUIREMENTS.md section 9 entry 29, "الطبقة 2" -- Protection
Classifier), each pattern deterministically selected via ``?pattern=``.
This is entry 29's own stated success criterion, verbatim: "اختبار يتأكد
إن response_classifier بيرجّع تصنيف مختلف وصحيح لكل نمط من التلاتة، بدليل
مباشر مش تخمين".

Deliberately NOT a ``scrapy runspider`` subprocess run (every other
mock-target-* live test in this package uses that shape) -- a real
``scrapy.http.Request``/``Response`` pair is still used (the real thing
``CircuitBreakerMiddleware.process_response`` is typed against, not a
loosened stand-in), but built directly from a real ``urllib.request``
call to the live stack (the same stdlib this project's own
``byparr_provider.py`` already uses for its own real HTTP calls) instead
of going through the full Scrapy engine. The reason is
``ResponsePattern.CHALLENGE_PAGE``'s own strategy
(``ResponseStrategy.TRY_ANTIBOT_PROVIDER``): a full Scrapy crawl would
let ``CircuitBreakerMiddleware``'s returned retry ``Request`` actually
re-enter the engine and trigger one real Byparr solve attempt against
this static page (there is nothing there for a real antibot provider to
meaningfully "solve") -- real, per this whole package's own "never fake
a live network call" discipline, but unrelated latency and failure
surface this specific test has no need to take on: the thing under
test is response_classifier's *classification*, proven with real data,
plus circuit_breaker's own dispatch decision (does it return a Request,
and with the right meta) -- not whether a live antibot-solve escalation
actually succeeds against a page that was never going to yield anything
different. That end-to-end question is out of this entry's own stated
scope.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:${ANUBIS_PORT:-8080}/ -- same "is a live-network CI
stack actually running" gate every other mock-target live test in this
package uses (this test calls no Byparr instance itself, but follows the
established convention rather than inventing a new env var for the same
underlying signal -- see test_mock_target_warmup_referer_live.py's own,
identical reasoning).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from scrapy.http import Request, Response

from src.diagnostics.failure_registry import PATH_ENV_VAR
from src.middlewares.circuit_breaker import CircuitBreakerMiddleware
from src.response_classifier import ResponsePattern, classify_response

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
MOCK_TARGET_BASE_URL = "http://localhost:8080"


def _fetch(pattern: str) -> tuple[int, dict[str, str], bytes]:
    """A real GET against the live mock-target stack's own
    ``/reject-pattern`` route -- status/headers/body straight from a real
    ``urllib.request`` call, no Scrapy involved yet."""
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern={pattern}"
    request = urllib.request.Request(url)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        # A 403 is a real, expected HTTPError from urllib's own
        # perspective (any non-2xx raises) -- the whole point of this
        # route, not a failure to fetch it.
        return exc.code, dict(exc.headers), exc.read()


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
@pytest.mark.parametrize(
    ("query_pattern", "expected_response_pattern"),
    [
        ("empty", ResponsePattern.SILENT_BLOCK),
        ("headers", ResponsePattern.HEADER_FINGERPRINTED),
        ("challenge", ResponsePattern.CHALLENGE_PAGE),
    ],
)
def test_classify_response_matches_the_real_mock_target_shape(
    query_pattern: str, expected_response_pattern: ResponsePattern
) -> None:
    """Direct evidence for entry 29's own success criterion: real status/
    headers/body from the live stack, classified correctly -- not a
    hand-built dict, not an assumption."""
    status, headers, body = _fetch(query_pattern)

    assert status == 403
    assert classify_response(headers, body) is expected_response_pattern


def test_silent_block_opens_the_circuit_via_circuit_breaker_against_a_real_response(
    tmp_path: Path,
) -> None:
    status, headers, body = _fetch("empty")
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern=empty"
    request = Request(url)
    response = Response(url=url, status=status, headers=headers, body=body, request=request)

    failure_log_path = tmp_path / "failure_log.jsonl"
    os.environ[PATH_ENV_VAR] = str(failure_log_path)
    try:
        middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0)
        result = middleware.process_response(request, response, spider=object())
    finally:
        os.environ.pop(PATH_ENV_VAR, None)

    assert isinstance(result, Response)
    circuit = middleware._circuits["localhost:8080"]
    assert circuit.state.value == "open"
    assert circuit.consecutive_failures == 1  # forced open, not threshold-triggered

    assert failure_log_path.exists()
    records = [
        json.loads(line) for line in failure_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["failure_category"] == "antibot-fingerprint-rejection"
    assert records[0]["raw_signal"]["reason"] == "classified_silent-block"


def test_challenge_page_returns_a_retry_request_via_circuit_breaker_against_a_real_response(
    tmp_path: Path,
) -> None:
    status, headers, body = _fetch("challenge")
    url = f"{MOCK_TARGET_BASE_URL}/reject-pattern?pattern=challenge"
    request = Request(url)
    response = Response(url=url, status=status, headers=headers, body=body, request=request)

    failure_log_path = tmp_path / "failure_log.jsonl"
    os.environ[PATH_ENV_VAR] = str(failure_log_path)
    try:
        middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0)
        result = middleware.process_response(request, response, spider=object())
    finally:
        os.environ.pop(PATH_ENV_VAR, None)

    # A Request, not a Response -- proves TRY_ANTIBOT_PROVIDER's own
    # dispatch actually fired for real data, without this test executing
    # the resulting antibot solve itself (see this module's own docstring
    # for why that's deliberately out of scope here).
    assert isinstance(result, Request)
    assert result.meta["antibot_needed"] is True
    assert result.meta["circuit_breaker_antibot_retried"] is True

    records = [
        json.loads(line) for line in failure_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["source"] == "circuit_breaker.retrying_via_antibot_provider"
    assert records[0]["raw_signal"]["response_pattern"] == "challenge-page"
