"""Structured (JSON) logging setup.

No module in this project should use ``print()`` for anything other than a
CLI entry point's final output — everything else goes through the logger
configured here so log lines are machine-parseable JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_CONFIGURED_LOGGERS: set[str] = set()

# Attributes every stdlib LogRecord carries; anything else on the record was
# passed by the caller via ``extra=`` and should be surfaced in the JSON.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JSONFormatter(logging.Formatter):
    """Render each :class:`logging.LogRecord` as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key in payload:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=_json_safe)


def _json_safe(value: Any) -> Any:
    """Best-effort conversion of a value into something ``json.dumps`` accepts.

    Never raises: a value that cannot be serialized falls back to ``repr()``
    so a logging call can never itself crash the caller.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a logger named ``name`` configured to emit JSON lines to stdout.

    Safe to call repeatedly for the same ``name``: the handler is attached
    only once per logger to avoid duplicate log lines.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if name not in _CONFIGURED_LOGGERS:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED_LOGGERS.add(name)

    return logger
