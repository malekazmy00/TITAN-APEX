"""Client-side automation detection via FingerprintJS's BotD (MIT license,
vendored at static/vendor/botd.esm.js -- see test-environment/README.md
section 1.3 for why it's vendored instead of loaded from a CDN: section 4's
network-isolation rule means the mock page can't reach the public
internet at render time).

BotD's verdict never blocks the request -- the layer only *observes*
whether our own scraper's rendering (Playwright, Byparr) looks
automated to a real detection library, logging the result rather than
enforcing it.
"""

from __future__ import annotations

import logging
from typing import Any

VENDORED_SCRIPT_PATH = "vendor/botd.esm.js"


def log_botd_report(logger: logging.Logger, report: dict[str, Any]) -> None:
    """Log one BotD client-side report; WARNING if a bot was flagged, INFO otherwise.

    Raises:
        TypeError: if ``report`` isn't a dict -- a malformed report can't
            be meaningfully logged as a detection result.
    """
    if not isinstance(report, dict):
        raise TypeError(f"report must be a dict, got {type(report).__name__}")

    bot_kind = report.get("bot")
    level = logging.WARNING if bot_kind else logging.INFO
    logger.log(level, "botd.report", extra={"report": report, "flagged": bool(bot_kind)})
