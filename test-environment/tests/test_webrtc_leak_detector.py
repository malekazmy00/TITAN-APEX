"""Unit tests for security/webrtc_leak_detector.py -- docs/
PHASE_2_BACKLOG.md item 5 (WebRTC Leak Prevention).
"""

from __future__ import annotations

import logging

import pytest
from security.webrtc_leak_detector import (
    CandidateClass,
    classify_candidate_address,
    extract_candidate_address,
    log_webrtc_leak_report,
    score_webrtc_report,
)

# Real-shaped SDP candidate lines (RFC 8839), one per class.
_HOST_CANDIDATE_LEAKED = "candidate:842163049 1 udp 2122260223 192.168.1.42 51823 typ host"
_HOST_CANDIDATE_MDNS = (
    "candidate:842163049 1 udp 2122260223 8f3e1a2b-71cd-4e21-9a4c-1a2b3c4d5e6f.local "
    "51823 typ host"
)
_HOST_CANDIDATE_LOOPBACK = "candidate:842163049 1 udp 2122260223 127.0.0.1 51823 typ host"


# --- extract_candidate_address --------------------------------------------


def test_extract_candidate_address_parses_a_real_host_candidate() -> None:
    assert extract_candidate_address(_HOST_CANDIDATE_LEAKED) == "192.168.1.42"


def test_extract_candidate_address_parses_an_mdns_candidate() -> None:
    address = extract_candidate_address(_HOST_CANDIDATE_MDNS)
    assert address is not None
    assert address.endswith(".local")


def test_extract_candidate_address_returns_none_for_malformed_input() -> None:
    assert extract_candidate_address("not a real candidate line") == None  # noqa: E711
    assert extract_candidate_address("") is None


# --- classify_candidate_address -------------------------------------------


def test_classify_candidate_address_flags_a_real_ip_as_leaked() -> None:
    assert classify_candidate_address("192.168.1.42") == CandidateClass.LEAKED
    assert classify_candidate_address("10.0.0.5") == CandidateClass.LEAKED


def test_classify_candidate_address_treats_mdns_as_safe() -> None:
    assert (
        classify_candidate_address("8f3e1a2b-71cd-4e21-9a4c-1a2b3c4d5e6f.local")
        == CandidateClass.MDNS
    )


def test_classify_candidate_address_treats_loopback_as_safe() -> None:
    assert classify_candidate_address("127.0.0.1") == CandidateClass.LOOPBACK
    assert classify_candidate_address("::1") == CandidateClass.LOOPBACK


# --- score_webrtc_report ----------------------------------------------------


def test_webrtc_unavailable_is_never_a_leak_regardless_of_candidates() -> None:
    """The strongest possible "not leaking" signal -- block_webrtc
    removed the API entirely, so nothing could have been gathered."""
    result = score_webrtc_report(webrtc_available=False, candidates=[_HOST_CANDIDATE_LEAKED])

    assert result.webrtc_available is False
    assert result.leak_detected is False


def test_a_real_ip_candidate_is_a_genuine_leak() -> None:
    result = score_webrtc_report(webrtc_available=True, candidates=[_HOST_CANDIDATE_LEAKED])

    assert result.leak_detected is True
    assert result.leaked_addresses == ["192.168.1.42"]


def test_mdns_only_candidates_are_not_a_leak() -> None:
    result = score_webrtc_report(webrtc_available=True, candidates=[_HOST_CANDIDATE_MDNS])

    assert result.leak_detected is False
    assert result.leaked_addresses == []


def test_loopback_only_candidates_are_not_a_leak() -> None:
    result = score_webrtc_report(webrtc_available=True, candidates=[_HOST_CANDIDATE_LOOPBACK])

    assert result.leak_detected is False


def test_a_mix_of_safe_and_leaked_candidates_still_flags_the_leak() -> None:
    result = score_webrtc_report(
        webrtc_available=True,
        candidates=[_HOST_CANDIDATE_MDNS, _HOST_CANDIDATE_LEAKED, _HOST_CANDIDATE_LOOPBACK],
    )

    assert result.leak_detected is True
    assert result.leaked_addresses == ["192.168.1.42"]
    assert result.candidate_classes == [
        CandidateClass.MDNS,
        CandidateClass.LEAKED,
        CandidateClass.LOOPBACK,
    ]


def test_no_candidates_at_all_is_not_a_leak() -> None:
    result = score_webrtc_report(webrtc_available=True, candidates=[])

    assert result.leak_detected is False


def test_unparseable_candidates_are_recorded_but_never_a_leak_by_themselves() -> None:
    result = score_webrtc_report(webrtc_available=True, candidates=["garbage"])

    assert result.leak_detected is False
    assert result.candidate_classes == [CandidateClass.UNPARSEABLE]


def test_rejects_a_non_list_candidates() -> None:
    with pytest.raises(TypeError, match="candidates must be a list"):
        score_webrtc_report(webrtc_available=True, candidates="not-a-list")  # type: ignore[arg-type]


# --- log_webrtc_leak_report -------------------------------------------------


def test_log_webrtc_leak_report_warns_when_a_leak_is_detected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.webrtc_leak.warn")
    with caplog.at_level(logging.INFO, logger="test.webrtc_leak.warn"):
        result = log_webrtc_leak_report(logger, True, [_HOST_CANDIDATE_LEAKED])

    assert result.leak_detected is True
    assert caplog.records[0].levelno == logging.WARNING


def test_log_webrtc_leak_report_stays_info_when_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.webrtc_leak.info")
    with caplog.at_level(logging.INFO, logger="test.webrtc_leak.info"):
        result = log_webrtc_leak_report(logger, True, [_HOST_CANDIDATE_MDNS])

    assert result.leak_detected is False
    assert caplog.records[0].levelno == logging.INFO
