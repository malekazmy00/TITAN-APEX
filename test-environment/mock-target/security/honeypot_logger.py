"""CRITICAL-level logging for a real honeypot hit.

A hit here means a client followed a link no visible/human interaction
could ever reach -- direct proof GenericSpider extracted (or, if it ever
gains click support, followed) something without checking visibility
first. See structural/honeypots.py and docs/REQUIREMENTS.md section 8.
"""

from __future__ import annotations

import logging


def log_honeypot_trigger(
    logger: logging.Logger,
    token: str,
    path: str,
    remote_addr: str | None,
    user_agent: str | None,
) -> None:
    """Log one honeypot hit at CRITICAL.

    Raises:
        ValueError: if ``token`` or ``path`` is empty -- there's no
            meaningful event to log without them.
    """
    if not token:
        raise ValueError("token must be non-empty")
    if not path:
        raise ValueError("path must be non-empty")

    logger.critical(
        "honeypot.triggered",
        extra={
            "token": token,
            "path": path,
            "remote_addr": remote_addr,
            "user_agent": user_agent,
        },
    )
