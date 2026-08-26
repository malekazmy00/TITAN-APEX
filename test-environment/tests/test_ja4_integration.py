"""Unit tests for mock-target/security/ja4_integration.py."""

from __future__ import annotations

from security.ja4_integration import log_ja4_fingerprint


class _FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict[str, object]]] = []

    def info(self, msg: str, extra: dict[str, object] | None = None) -> None:
        self.info_calls.append((msg, extra or {}))


def test_a_real_fingerprint_is_logged() -> None:
    """Happy path: the real JA4-proxy-set header value is logged verbatim."""
    fake_logger = _FakeLogger()

    log_ja4_fingerprint(fake_logger, "t13d1516h2_8daaf6152771_02713d6af862")  # type: ignore[arg-type]

    assert fake_logger.info_calls == [
        (
            "ja4.fingerprint_observed",
            {"ja4_fingerprint": "t13d1516h2_8daaf6152771_02713d6af862"},
        )
    ]


def test_no_header_at_all_logs_nothing() -> None:
    """Every existing route (never reached through the JA4 proxy) hits
    this -- deliberately silent, not a warning or an info line, to
    avoid drowning every other live test's own logs in a pure no-op."""
    fake_logger = _FakeLogger()

    log_ja4_fingerprint(fake_logger, None)  # type: ignore[arg-type]

    assert fake_logger.info_calls == []


def test_an_empty_string_header_logs_nothing() -> None:
    """Failure-adjacent case: a present-but-empty header value is
    treated the same as absent, not as a real (empty) fingerprint."""
    fake_logger = _FakeLogger()

    log_ja4_fingerprint(fake_logger, "")  # type: ignore[arg-type]

    assert fake_logger.info_calls == []
