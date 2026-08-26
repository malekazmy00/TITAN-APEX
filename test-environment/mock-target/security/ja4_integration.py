"""JA4 TLS fingerprint logging -- docs/REQUIREMENTS.md section 9 entry
17 (F5-class multi-signal layer, claude/ja4-experiment branch).

The fingerprint itself is computed upstream, at the network layer,
before any request reaches this Flask app at all -- see
test-environment/ja4-proxy/haproxy.cfg's own docstring for the real
TLS-termination + JA4-computation mechanics (FriendlyCaptcha's
ja4.lua, vendored verbatim). This module's only job is the same one
security/botd_integration.py already has for BotD: observe and log
what arrived, never enforce anything by itself. A request that never
went through the JA4 proxy (i.e. every existing plain-http:// route,
reached directly via Anubis's own :8080) simply carries no such
header at all -- a real, structural no-op for all of them, not a
runtime check.
"""

from __future__ import annotations

import logging

JA4_HEADER_NAME = "X-JA4-Fingerprint"


def log_ja4_fingerprint(logger: logging.Logger, fingerprint: str | None) -> None:
    """Logs one request's JA4 fingerprint, if it has one.

    ``fingerprint`` is whatever ``JA4_HEADER_NAME`` carried on this
    request (``None`` if the header was absent -- a request that
    reached this app directly via Anubis's own plain :8080 listener,
    never through the JA4 proxy). Absent is the real, expected case for
    every existing route and deliberately not logged at all -- every
    one of this stack's other 37+ live tests hits this app many times
    per run, and logging a no-op for each would be pure noise, not a
    real observation. This layer classifies nothing yet
    (docs/REQUIREMENTS.md entry 17's own step-by-step plan defers the
    "known-automation-fingerprint -> suspicious" rule to a later
    step), it only observes when there's something real to observe.
    """
    if fingerprint:
        logger.info("ja4.fingerprint_observed", extra={"ja4_fingerprint": fingerprint})
