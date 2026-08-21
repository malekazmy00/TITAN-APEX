"""One-JSON-line-per-event file logger for the security layers.

Deliberately independent of src/logging_config.py's JSONFormatter (that
one is designed to write to stdout for the *product*; this writes to a
dedicated file per security signal -- test-environment is test
infrastructure *around* src/, not a dependency of it, and keeping the two
decoupled means neither can accidentally break the other).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Maps logger name -> the log_path its current handler writes to, so a
# second call with a *different* path for the same name reconfigures
# rather than silently keeping the stale handler (this is what a process
# that legitimately re-points its logging setup needs -- e.g. this
# module's own test suite building several MockTargetConfig instances
# with different tmp_path log files in one pytest process).
_CONFIGURED_LOGGERS: dict[str, str] = {}
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class _JSONLineFormatter(logging.Formatter):
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
        return json.dumps(payload, default=_json_safe)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def get_file_logger(name: str, log_path: str, level: str = "INFO") -> logging.Logger:
    """Return a logger named ``name`` that appends one JSON line per event to ``log_path``.

    Safe to call repeatedly for the same ``(name, log_path)`` pair: the
    handler is attached only once, so re-calling never duplicates log
    lines. Calling again with the *same* name but a *different*
    ``log_path`` closes the old handler and points the logger at the new
    file instead of silently keeping the stale one.

    Raises:
        OSError: if ``log_path``'s parent directory cannot be created (e.g.
            a permissions problem) -- never swallowed, since a security
            layer that silently fails to log defeats its own purpose.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if _CONFIGURED_LOGGERS.get(name) != log_path:
        for old_handler in list(logger.handlers):
            logger.removeHandler(old_handler)
            old_handler.close()

        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(_JSONLineFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED_LOGGERS[name] = log_path

    return logger
