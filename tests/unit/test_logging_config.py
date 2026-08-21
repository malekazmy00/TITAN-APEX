"""Unit tests for src/logging_config.py."""

from __future__ import annotations

import json
import logging

from src.logging_config import get_logger


def test_log_record_is_emitted_as_valid_json(capsys) -> None:  # type: ignore[no-untyped-def]
    """Happy path: a log call produces one line of valid, well-shaped JSON."""
    logger = get_logger("test.happy_path.capsys")
    logger.info("hello world", extra={"target": "quotes_toscrape"})

    out = capsys.readouterr().err
    line = out.strip().splitlines()[-1]
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.happy_path.capsys"
    assert payload["message"] == "hello world"
    assert payload["target"] == "quotes_toscrape"
    assert "timestamp" in payload


def test_logging_an_exception_includes_a_formatted_traceback(capsys) -> None:  # type: ignore[no-untyped-def]
    """Failure case 1: exc_info is captured as a string, never crashes the logger."""
    logger = get_logger("test.failure.exception")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.error("operation failed", exc_info=True)

    out = capsys.readouterr().err
    payload = json.loads(out.strip().splitlines()[-1])

    assert "exception" in payload
    assert "ValueError: boom" in payload["exception"]


def test_logging_a_non_json_serializable_extra_falls_back_to_repr(capsys) -> None:  # type: ignore[no-untyped-def]
    """Failure case 2: an unserializable ``extra`` value never raises; it degrades to repr()."""
    logger = get_logger("test.failure.non_serializable")

    class Unserializable:
        def __repr__(self) -> str:
            return "<Unserializable instance>"

    logger.warning("weird payload", extra={"payload": Unserializable()})

    out = capsys.readouterr().err
    payload = json.loads(out.strip().splitlines()[-1])

    assert payload["payload"] == "<Unserializable instance>"


def test_calling_get_logger_twice_does_not_duplicate_handlers() -> None:
    """Same logger name configured twice must not double-log."""
    logger1 = get_logger("test.no_dup_handlers")
    logger2 = get_logger("test.no_dup_handlers")

    assert logger1 is logger2
    assert len(logger1.handlers) == 1
    assert isinstance(logger1.handlers[0], logging.StreamHandler)
