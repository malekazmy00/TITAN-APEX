from __future__ import annotations

import pytest

from src.response_classifier import (
    DEFAULT_STRATEGY_FOR_PATTERN,
    ResponsePattern,
    ResponseStrategy,
    classify_response,
    strategy_for,
)


class TestClassifyResponse:
    def test_empty_body_and_no_known_header_is_silent_block(self) -> None:
        assert classify_response(headers={}, body=b"") is ResponsePattern.SILENT_BLOCK

    def test_whitespace_only_body_is_silent_block(self) -> None:
        assert classify_response(headers={}, body="   \n\t  ") is ResponsePattern.SILENT_BLOCK

    def test_known_block_header_is_header_fingerprinted(self) -> None:
        result = classify_response(headers={"X-Antibot-Block": "titan-apex-mock"}, body=b"")
        assert result is ResponsePattern.HEADER_FINGERPRINTED

    def test_known_header_match_is_case_insensitive_on_name_and_lookup(self) -> None:
        # Real header names arrive in whatever casing the server sent
        # (RFC 7230 section 3.2: header field names are case-insensitive)
        # -- this must not depend on the caller normalizing first.
        result = classify_response(headers={"cF-mItIgAtEd": "challenge"}, body=b"")
        assert result is ResponsePattern.HEADER_FINGERPRINTED

    def test_known_header_wins_over_a_body_that_also_has_a_challenge_marker(self) -> None:
        # Priority order (this module's own docstring): a named header is
        # checked *before* body shape, since it is the more specific signal.
        result = classify_response(
            headers={"x-datadome": "1"},
            body="<html>Access Denied</html>",
        )
        assert result is ResponsePattern.HEADER_FINGERPRINTED

    def test_challenge_marker_in_body_is_challenge_page(self) -> None:
        body = "<html><body>Please verify you are human to continue.</body></html>"
        assert classify_response(headers={}, body=body) is ResponsePattern.CHALLENGE_PAGE

    def test_challenge_marker_match_is_case_insensitive(self) -> None:
        body = "<html><body>ACCESS DENIED</body></html>"
        assert classify_response(headers={}, body=body) is ResponsePattern.CHALLENGE_PAGE

    def test_challenge_marker_detected_in_bytes_body(self) -> None:
        body = b"<html>Checking your browser before accessing...</html>"
        assert classify_response(headers={}, body=body) is ResponsePattern.CHALLENGE_PAGE

    def test_unrecognized_body_with_no_marker_and_no_known_header(self) -> None:
        body = "<html><body>Some other, unrelated non-empty page.</body></html>"
        assert classify_response(headers={}, body=body) is ResponsePattern.UNRECOGNIZED

    def test_unrecognized_takes_an_unrelated_header_too(self) -> None:
        result = classify_response(
            headers={"Content-Type": "text/html"},
            body="<html><body>Some other, unrelated non-empty page.</body></html>",
        )
        assert result is ResponsePattern.UNRECOGNIZED


class TestStrategyFor:
    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            (ResponsePattern.SILENT_BLOCK, ResponseStrategy.IMMEDIATE_LONG_BACKOFF),
            (ResponsePattern.CHALLENGE_PAGE, ResponseStrategy.TRY_ANTIBOT_PROVIDER),
            (ResponsePattern.HEADER_FINGERPRINTED, ResponseStrategy.STANDARD),
            (ResponsePattern.UNRECOGNIZED, ResponseStrategy.STANDARD),
        ],
    )
    def test_default_strategy_matches_the_layer_2_spec(
        self, pattern: ResponsePattern, expected: ResponseStrategy
    ) -> None:
        assert strategy_for(pattern) is expected

    def test_every_response_pattern_has_a_default_strategy(self) -> None:
        # Defensive against a future ResponsePattern member added without
        # a matching entry in DEFAULT_STRATEGY_FOR_PATTERN.
        for pattern in ResponsePattern:
            assert pattern in DEFAULT_STRATEGY_FOR_PATTERN

    def test_override_takes_precedence_over_the_default(self) -> None:
        overrides = {ResponsePattern.HEADER_FINGERPRINTED: ResponseStrategy.IMMEDIATE_LONG_BACKOFF}
        result = strategy_for(ResponsePattern.HEADER_FINGERPRINTED, overrides=overrides)
        assert result is ResponseStrategy.IMMEDIATE_LONG_BACKOFF

    def test_override_mapping_not_containing_the_pattern_falls_back_to_default(self) -> None:
        overrides = {ResponsePattern.SILENT_BLOCK: ResponseStrategy.STANDARD}
        result = strategy_for(ResponsePattern.CHALLENGE_PAGE, overrides=overrides)
        assert result is ResponseStrategy.TRY_ANTIBOT_PROVIDER

    def test_none_overrides_uses_the_default(self) -> None:
        result = strategy_for(ResponsePattern.SILENT_BLOCK, overrides=None)
        assert result is ResponseStrategy.IMMEDIATE_LONG_BACKOFF
