"""Unit tests for mock-target/security/referer_session_integration.py."""

from __future__ import annotations

from security.referer_session_integration import (
    WARMUP_SESSION_COOKIE_NAME,
    log_referer_session_check,
    score_referer_path_consistency,
    score_referer_shape,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, msg: str, extra: dict[str, object] | None = None) -> None:
        self.calls.append((msg, extra or {}))


# --- Level 1: score_referer_shape --------------------------------------


def test_score_referer_shape_is_zero_when_missing() -> None:
    """A missing Referer is the normal start of a real browsing chain
    (Scrapfly's own quoted source, this module's own docstring) --
    deliberately not a Level 1 violation on its own."""
    assert score_referer_shape(None) == 0
    assert score_referer_shape("") == 0


def test_score_referer_shape_is_zero_for_a_well_formed_url() -> None:
    assert score_referer_shape("http://localhost:8080/warmup-home") == 0


def test_score_referer_shape_is_one_for_a_malformed_value() -> None:
    """A present-but-garbage Referer (no scheme/host at all) is a real
    shape violation, unlike a missing one."""
    assert score_referer_shape("not-a-url-at-all") == 1
    assert score_referer_shape("/just-a-path") == 1


# --- Level 2: score_referer_path_consistency ----------------------------


def test_score_referer_path_consistency_is_zero_for_the_entry_point() -> None:
    """/warmup-home has no entry in VALID_PREDECESSOR_PATHS -- there is
    no predecessor to require at all, regardless of Referer/cookie."""
    assert score_referer_path_consistency("/warmup-home", None, False) == 0


def test_score_referer_path_consistency_is_zero_on_a_real_clean_visit() -> None:
    """Happy path: the real predecessor path, plus a warm-up cookie
    already present -- a genuinely connected, warmed-up visit."""
    score = score_referer_path_consistency(
        "/warmup-category", "http://localhost:8080/warmup-home", True
    )

    assert score == 0


def test_score_referer_path_consistency_flags_a_mismatched_referer_path() -> None:
    score = score_referer_path_consistency(
        "/warmup-category", "http://localhost:8080/some-unrelated-page", True
    )

    assert score == 1


def test_score_referer_path_consistency_flags_a_missing_referer_on_a_deep_path() -> None:
    """Unlike Level 1, a missing Referer on a page that expects a real
    predecessor *is* a violation here -- this module's own docstring
    explains the contextual distinction."""
    score = score_referer_path_consistency("/warmup-category", None, True)

    assert score == 1


def test_score_referer_path_consistency_flags_a_missing_session_cookie() -> None:
    score = score_referer_path_consistency(
        "/warmup-category", "http://localhost:8080/warmup-home", False
    )

    assert score == 1


def test_score_referer_path_consistency_counts_both_signals_independently() -> None:
    """The actual point of a score, not a verdict: both a mismatched
    Referer and a missing cookie together score higher than either
    alone."""
    score = score_referer_path_consistency(
        "/warmup-category", "http://localhost:8080/some-unrelated-page", False
    )

    assert score == 2


def test_score_referer_path_consistency_accepts_a_deep_page_as_its_own_predecessor() -> None:
    """/warmup-target accepts itself as a valid predecessor (a real
    reload/re-visit of the same deep page) -- see this module's own
    docstring for why."""
    score = score_referer_path_consistency(
        "/warmup-target", "http://localhost:8080/warmup-target", True
    )

    assert score == 0


# --- log_referer_session_check ------------------------------------------


def test_log_referer_session_check_always_logs_at_info() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21: deliberately never
    WARNING/ERROR yet -- same log-only-first principle
    fpscanner_integration.py's own module docstring already documents
    (entry 19's own Microsoft/F5 citations)."""
    fake_logger = _FakeLogger()

    log_referer_session_check(
        fake_logger,  # type: ignore[arg-type]
        "/warmup-category",
        "http://localhost:8080/some-unrelated-page",
        False,
    )

    message, extra = fake_logger.calls[0]
    assert message == "referer_session.checked"
    assert extra["path"] == "/warmup-category"
    assert extra["referer"] == "http://localhost:8080/some-unrelated-page"
    assert extra["has_warmup_session_cookie"] is False
    assert extra["level1_score"] == 0
    assert extra["level2_score"] == 2


def test_log_referer_session_check_on_a_clean_visit() -> None:
    fake_logger = _FakeLogger()

    log_referer_session_check(
        fake_logger,  # type: ignore[arg-type]
        "/warmup-category",
        "http://localhost:8080/warmup-home",
        True,
    )

    _, extra = fake_logger.calls[0]
    assert extra["level1_score"] == 0
    assert extra["level2_score"] == 0


def test_warmup_session_cookie_name_is_a_real_constant() -> None:
    """Regression sentinel: app.py imports this constant directly --
    a rename here without updating app.py would otherwise only surface
    as a silent, always-False cookie check."""
    assert WARMUP_SESSION_COOKIE_NAME == "mocktarget_warmup_session"
