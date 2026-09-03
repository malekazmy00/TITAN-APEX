"""Unit tests for src/diagnostics/failure_taxonomy.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord, ResolutionStatus


def test_failure_category_values_match_the_literal_taxonomy_given() -> None:
    """The original 8 category strings (plus NO_SCROLLABLE_CONTENT, the
    9th, added while wiring "الطبقة 2" -- see that member's own
    docstring) must match exactly what was specified -- a future
    consumer (a report, Layer 2/3) matches against these literal values,
    so a silent rename here would break that matching without any type
    error to catch it."""
    assert {member.value for member in FailureCategory} == {
        "antibot-fingerprint-rejection",
        "timing-race",
        "session-expired",
        "structural-selector-mismatch",
        "rate-limited",
        "network-infra-transient",
        "external-site-flake",
        "unknown",
        "no-scrollable-content",
    }


def test_resolution_status_values() -> None:
    assert {member.value for member in ResolutionStatus} == {
        "unresolved",
        "known-limitation",
        "resolved",
    }


def test_failure_record_happy_path_with_every_field_set() -> None:
    """Happy path: a fully-populated record round-trips through validation."""
    record = FailureRecord(
        timestamp=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        target="https://example.com/",
        provider="camoufox",
        failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        raw_signal={"status_code": 403, "message": "challenge not solvable"},
        resolution_status=ResolutionStatus.KNOWN_LIMITATION,
        source="camoufox_provider.solve_failed",
    )

    assert record.target == "https://example.com/"
    assert record.provider == "camoufox"
    assert record.failure_category is FailureCategory.ANTIBOT_FINGERPRINT_REJECTION
    assert record.raw_signal == {"status_code": 403, "message": "challenge not solvable"}
    assert record.resolution_status is ResolutionStatus.KNOWN_LIMITATION
    assert record.source == "camoufox_provider.solve_failed"


def test_failure_record_defaults_provider_to_none_and_resolution_to_unresolved() -> None:
    """A provider-agnostic failure (rate limiter, circuit breaker) has no
    provider at all -- must default to None, not an empty string or a
    required field. resolution_status must default to the honest
    'nobody has decided yet' state, not silently 'resolved'."""
    record = FailureRecord(
        timestamp=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        target="example.com",
        failure_category=FailureCategory.RATE_LIMITED,
        source="rate_limiter.blocked",
    )

    assert record.provider is None
    assert record.resolution_status is ResolutionStatus.UNRESOLVED
    assert record.raw_signal == {}


def test_failure_record_rejects_an_invalid_category() -> None:
    """Failure case: a category string outside the enum must be rejected,
    not silently coerced -- this is exactly the guardrail that keeps
    Layer 2/3 from ever seeing an uncategorized-but-claims-to-be-typed
    record."""
    with pytest.raises(ValidationError):
        FailureRecord(
            timestamp=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
            target="example.com",
            failure_category="not-a-real-category",  # type: ignore[arg-type]
            source="test",
        )


def test_failure_record_requires_source() -> None:
    """source is not optional -- a record with no provenance at all is
    exactly the "can't audit or trust this" gap this module's own
    docstring names."""
    with pytest.raises(ValidationError):
        FailureRecord(  # type: ignore[call-arg]
            timestamp=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
            target="example.com",
            failure_category=FailureCategory.UNKNOWN,
        )
