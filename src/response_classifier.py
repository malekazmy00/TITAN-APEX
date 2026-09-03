"""Pure classification of a rejected HTTP response into a known pattern.

docs/REQUIREMENTS.md section 9, "الطبقة 2" (Protection Classifier) --
an explicit extension of the existing per-domain
:class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`, not a
replacement for it. That middleware already tracks plain 5xx statuses and
request-level exceptions as generic "failures" (``FAILURE_STATUSES`` in
that module); this module is about a different, narrower question: when a
target rejects a request with an antibot-style status (403/429/... --
``CircuitBreakerMiddleware.CLASSIFIABLE_STATUSES``), *what does the
rejection itself look like*? A raw status code alone cannot tell a
silent, zero-explanation block apart from a target that at least shows a
readable challenge page -- and those two cases warrant genuinely
different response strategies (see :data:`DEFAULT_STRATEGY_FOR_PATTERN`
below), which is the whole reason this stays a separate, focused module
called *by* circuit_breaker.py rather than folded directly into it.

This module has no Scrapy import and no I/O -- ``classify_response`` is a
plain function over ``headers``/``body`` values a caller already has, kept
this way deliberately so it is trivially unit-testable with hand-built
dicts/strings, independent of any live request/response object shape.

Three named patterns, checked by :func:`classify_response` in this order
(order matters -- see that function's own docstring for why):

- :attr:`ResponsePattern.HEADER_FINGERPRINTED` -- a header named in
  :data:`KNOWN_BLOCK_HEADERS` is present, regardless of body content.
  Checked *first* because a real vendor/WAF header is the single most
  specific signal available: some real anti-bot vendors (Cloudflare's own
  ``cf-mitigated: challenge``/``cf-mitigated: block``, still current at
  the time this was written) fire this on an otherwise empty or
  ambiguous body, so body-shape checks alone would misclassify it as
  :attr:`ResponsePattern.SILENT_BLOCK` instead.
- :attr:`ResponsePattern.SILENT_BLOCK` -- the body is empty (or
  whitespace-only) and no known header fired. No explanation offered at
  all: the response carries zero information beyond "no".
- :attr:`ResponsePattern.CHALLENGE_PAGE` -- the body is non-empty and
  contains at least one of the known challenge/branding markers in
  :data:`KNOWN_CHALLENGE_MARKERS` -- a real, human-readable page
  explaining (even just by product/vendor name) why access was denied.
- :attr:`ResponsePattern.UNRECOGNIZED` -- none of the above matched. A
  real, honest fallback, never silently forced into one of the three
  named patterns just because a decision had to be made -- mirrors
  ``FailureCategory.UNKNOWN``'s own role in
  ``src/diagnostics/failure_taxonomy.py`` (docs/REQUIREMENTS.md section 9
  entry 28, "الطبقة 1"): an explicit "we don't recognize this shape" is
  more honest than a guess, and a future layer can extend the known
  lists below, or add new patterns entirely, without needing to touch
  every call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

# Real-world vendor/WAF response-header names known to mark a request as
# explicitly, distinctly blocked -- matched case-insensitively by
# classify_response (HTTP header names are case-insensitive per RFC 7230
# section 3.2, so a plain-dict caller's exact casing must never matter
# here). Not exhaustive -- no fixed list of every anti-bot vendor's
# headers could be -- extending it is a one-line addition, the same
# "known, named signature" shape KNOWN_CHALLENGE_MARKERS below has.
KNOWN_BLOCK_HEADERS: frozenset[str] = frozenset(
    {
        "cf-mitigated",  # Cloudflare -- fires with value "challenge" or "block"
        "x-datadome",  # DataDome
        "x-px-block-reason",  # PerimeterX / HUMAN Security
        # This project's own deterministic test fixture (not a real-world
        # vendor) -- test-environment/mock-target/app.py's own
        # /reject-pattern?pattern=headers route sets exactly this header,
        # so the live integration test proves this whole module against
        # a real HTTP response, not just hand-built dicts. See that
        # route's own docstring.
        "x-antibot-block",
    }
)

# Case-insensitive substrings looked for in the response body -- real
# enough to be plausible (a WAF/CDN's own name, or a generic "you've been
# blocked / verify you're human" challenge phrase) without hardcoding any
# single vendor's exact copy.
KNOWN_CHALLENGE_MARKERS: tuple[str, ...] = (
    "checking your browser",
    "verify you are human",
    "access denied",
    "attention required",
    "cloudflare",
    # This project's own deterministic test fixture -- see
    # test-environment/mock-target/app.py's /reject-pattern?pattern=challenge
    # route, matched by response_classifier's own live integration test.
    "titan-apex-mock-challenge",
)


class ResponsePattern(StrEnum):
    """A rejected response's classified shape.

    Feeds :data:`DEFAULT_STRATEGY_FOR_PATTERN` (this module) and, from
    there, :class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`'s
    own per-domain response handling.
    """

    HEADER_FINGERPRINTED = "header-fingerprinted"
    SILENT_BLOCK = "silent-block"
    CHALLENGE_PAGE = "challenge-page"
    UNRECOGNIZED = "unrecognized"


class ResponseStrategy(StrEnum):
    """What CircuitBreakerMiddleware should do next for a given
    :class:`ResponsePattern` -- configurable per pattern (see
    :data:`DEFAULT_STRATEGY_FOR_PATTERN` and
    ``CircuitBreakerMiddleware``'s own ``strategy_overrides`` constructor
    parameter), never logic hardcoded inside the middleware itself.
    """

    #: Open the circuit immediately, skipping the normal
    #: failure_threshold count entirely, with an extended cooldown --
    #: docs/REQUIREMENTS.md's own Layer 2 spec, verbatim: "نمط 'فاضي بلا
    #: علامة' = backoff طويل فورًا".
    IMMEDIATE_LONG_BACKOFF = "immediate-long-backoff"
    #: Re-issue the request with antibot solving turned on --
    #: docs/REQUIREMENTS.md's own Layer 2 spec, verbatim: "نمط 'صفحة تحدي
    #: واضحة' = جرّب antibot provider".
    TRY_ANTIBOT_PROVIDER = "try-antibot-provider"
    #: The existing, already-correct circuit-breaker behavior (count
    #: toward consecutive_failures, open only at failure_threshold) --
    #: the deliberate default for any pattern the Layer 2 spec did not
    #: name a specific strategy for (see DEFAULT_STRATEGY_FOR_PATTERN's
    #: own comment for why HEADER_FINGERPRINTED/UNRECOGNIZED default
    #: here instead of a guessed strategy).
    STANDARD = "standard"


# docs/REQUIREMENTS.md's own Layer 2 spec gave an explicit strategy for
# exactly two of the four patterns: SILENT_BLOCK and CHALLENGE_PAGE (see
# each ResponseStrategy member's own docstring above for the verbatim
# text). HEADER_FINGERPRINTED and UNRECOGNIZED were not given one --
# both default to STANDARD rather than guessing: a named vendor header
# alone doesn't tell us whether *this* target even has a working antibot
# provider path configured (unlike CHALLENGE_PAGE, which is direct
# evidence a browser-rendered challenge exists to solve), and
# UNRECOGNIZED is by definition not confidently anything. Overridable
# per pattern via CircuitBreakerMiddleware's own strategy_overrides
# constructor parameter without touching this module.
DEFAULT_STRATEGY_FOR_PATTERN: Mapping[ResponsePattern, ResponseStrategy] = {
    ResponsePattern.SILENT_BLOCK: ResponseStrategy.IMMEDIATE_LONG_BACKOFF,
    ResponsePattern.CHALLENGE_PAGE: ResponseStrategy.TRY_ANTIBOT_PROVIDER,
    ResponsePattern.HEADER_FINGERPRINTED: ResponseStrategy.STANDARD,
    ResponsePattern.UNRECOGNIZED: ResponseStrategy.STANDARD,
}


def classify_response(headers: Mapping[str, str], body: bytes | str) -> ResponsePattern:
    """Classifies a single already-known-to-be-rejected response.

    This function has no opinion on *which* HTTP status codes count as a
    rejection in the first place -- that's
    ``CircuitBreakerMiddleware.CLASSIFIABLE_STATUSES``'s decision, made
    before this is ever called (docs/REQUIREMENTS.md's own examples,
    "403/429/إلخ", describe status codes that warrant classification, not
    codes this function itself inspects).

    ``headers`` keys are matched case-insensitively regardless of the
    input mapping's own casing (a plain ``dict`` is fine to pass here;
    ``CircuitBreakerMiddleware`` normalizes Scrapy's own bytes-keyed
    ``Response.headers`` via ``to_unicode_dict()`` before calling this --
    see that module). Checked in a fixed priority order: a known header
    always wins over body shape, since a real vendor signature is more
    specific evidence than an empty or ambiguous body alone (see this
    module's own docstring for a real-world example of why).
    """
    lower_header_names = {name.lower() for name in headers}
    if lower_header_names & KNOWN_BLOCK_HEADERS:
        return ResponsePattern.HEADER_FINGERPRINTED

    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if not text.strip():
        return ResponsePattern.SILENT_BLOCK

    lower_text = text.lower()
    if any(marker in lower_text for marker in KNOWN_CHALLENGE_MARKERS):
        return ResponsePattern.CHALLENGE_PAGE

    return ResponsePattern.UNRECOGNIZED


def strategy_for(
    pattern: ResponsePattern,
    overrides: Mapping[ResponsePattern, ResponseStrategy] | None = None,
) -> ResponseStrategy:
    """Resolves ``pattern``'s :class:`ResponseStrategy`, consulting
    ``overrides`` first (a config-driven per-target/per-domain map, e.g.
    ``CircuitBreakerMiddleware``'s own ``strategy_overrides``) and falling
    back to :data:`DEFAULT_STRATEGY_FOR_PATTERN`. A pattern present in
    neither still resolves rather than raising -- defensive against a
    future :class:`ResponsePattern` member added without a matching
    default (falls back to :attr:`ResponseStrategy.STANDARD`, the same
    "no special-case, just count it normally" behavior an unmapped
    pattern should always get).
    """
    if overrides is not None and pattern in overrides:
        return overrides[pattern]
    return DEFAULT_STRATEGY_FOR_PATTERN.get(pattern, ResponseStrategy.STANDARD)
