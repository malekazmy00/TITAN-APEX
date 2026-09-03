"""Unified failure taxonomy -- Phase 3 item, "Layer 1" of a 3-layer
diagnosis-and-decision system the user requested explicitly (Layer 2:
a Protection Classifier already planned as Phase 3 item 3; Layer 3: a
Strategy Engine that decides what to do about a classified failure).
This module is the schema only -- see :mod:`src.diagnostics.failure_registry`
for the durable JSONL writer/reader built on it.

**Why this exists:** this project already has real, working diagnostics
scattered across many modules -- ``scroll_diagnostics``
(``playwright_middleware.py``), ``apparmor_denials_during_solve``
(``camoufox_provider.py``, via ``_tracing.py``'s
``count_apparmor_camoufox_denials``/``apparmor_denial_delta``), Byparr/
Patchright/Anubis rejection reasons (each provider's own
``solve_failed``-shaped log lines), and ``CircuitBreakerMiddleware``'s
open/close events -- each one real, each one already logged as
structured JSON (``src/logging_config.py``), but each one only visible
in that one CI run's own ephemeral job log, in its own shape, with no
shared vocabulary connecting "this scroll got 0 requests" to "this
circuit breaker opened" to "this challenge was never solved" as
instances of a *general* concept: a crawl attempt failed, and it failed
*for some reason a person or a future automated system needs to be able
to categorize, count, and eventually act on*. Nothing before this
module lets you ask "how many failures this project has ever hit are a
site correctly detecting the crawler, versus this project's own code
racing a timer it shouldn't be racing?" across the whole codebase at
once -- every prior answer to that question (docs/REQUIREMENTS.md's own
entries 1-27) required a person to read prose and remember.

**Deliberately narrow scope for this module:** just the shape of one
failure record. It does not decide what a *provider* is (an
``AntibotProvider`` name, or ``None`` for a provider-agnostic failure
like a circuit breaker opening), does not decide *how* a failure gets
written durably (that is ``failure_registry.py``'s job, kept separate
so the schema itself has zero I/O and is trivially unit-testable), and
does not yet decide what to *do* about a classified failure (Layer 3).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FailureCategory(StrEnum):
    """The 8 categories the user specified explicitly, verbatim, plus one
    9th added deliberately during "الطبقة 2" (Protection Classifier) --
    this enum's own value strings are the literal taxonomy names given,
    not a paraphrase, so a historical classification or a future Layer
    2/3 consumer can match against them exactly.

    - ``ANTIBOT_FINGERPRINT_REJECTION``: the target correctly detected
      this as automated traffic and refused to serve real content --
      a real, working defense, not a bug on either side (e.g. Byparr's
      "challenge not solvable", a User-Agent/header content check
      failing, an Anubis challenge never resolving).
    - ``TIMING_RACE``: the crawl's *own* timing assumption lost a race
      against the target's real, variable timing (e.g. a scroll trigger
      firing before/after the exact moment a page's own JS was ready
      for it, entry 17's whole DOM Virtualization investigation).
    - ``SESSION_EXPIRED``: a previously-valid session (cookie, login
      state) stopped being accepted mid-crawl -- distinct from a
      fingerprint rejection (the session *was* real and valid once).
    - ``STRUCTURAL_SELECTOR_MISMATCH``: the page's real DOM/JSON shape
      doesn't match what a config's selectors assume (a real layout
      change, a config written against the wrong assumption, a CSS-in-JS
      hashed class name -- entry 5/23's whole story).
    - ``RATE_LIMITED``: this project's own ``RateLimiterMiddleware``
      (or a target's real 429/Retry-After) rejected/delayed a request
      for exceeding a rate budget -- self-imposed or target-imposed,
      both land here since the *symptom* (a request didn't go out when
      requested) is identical either way.
    - ``NETWORK_INFRA_TRANSIENT``: a real, non-target-specific
      environment/infrastructure hiccup -- a browser engine crash, a
      sandbox constraint (AppArmor, a proxy CA not trusted), a plain
      connection reset -- the kind of failure this project has
      historically documented as "register it and leave it for the
      real deploy environment to confirm," never a target's own defense
      or this project's own logic bug.
    - ``EXTERNAL_SITE_FLAKE``: the target itself behaved inconsistently
      run to run, for reasons that are real but not attributable to any
      of the categories above (a site's own transient 5xx unrelated to
      rate limiting, an A/B-tested page variant, a race entirely inside
      the target's own backend). Distinct from ``TIMING_RACE``: that
      category is about *this project's* timing assumption losing a
      race; this one is about the *target itself* being inconsistent
      for reasons outside this project's control or knowledge.
    - ``UNKNOWN``: none of the above fit, or there isn't yet enough
      evidence to tell -- an honest placeholder, never silently
      defaulted to without being visible as exactly that.
    - ``NO_SCROLLABLE_CONTENT``: the 9th category, added while wiring
      "الطبقة 2" (docs/REQUIREMENTS.md section 9 entry 29) after a real
      CI run's own data (entry 28's own CI-confirmation writeup) caught
      a genuine false positive in the ``TIMING_RACE`` guard added by
      that same commit: ``requests_during_scroll == 0`` alone cannot
      distinguish "the scroll trigger should have loaded more content
      but didn't" (a real timing race) from "this page simply has no
      scroll-triggered content at all" (``initial_height ==
      final_height`` from the very first measurement -- nothing was
      ever going to load, scroll or no scroll). Distinct from
      ``TIMING_RACE`` on purpose rather than folded into it with a
      flag: a *category* that structurally cannot represent a project
      bug (there is nothing to fix about a page that was never going to
      scroll-load anything) keeps the taxonomy itself honest, the same
      way ``EXTERNAL_SITE_FLAKE`` is kept distinct from ``TIMING_RACE``
      above for an analogous reason (whose "side" the cause is
      attributable to).
    """

    ANTIBOT_FINGERPRINT_REJECTION = "antibot-fingerprint-rejection"
    TIMING_RACE = "timing-race"
    SESSION_EXPIRED = "session-expired"
    STRUCTURAL_SELECTOR_MISMATCH = "structural-selector-mismatch"
    RATE_LIMITED = "rate-limited"
    NETWORK_INFRA_TRANSIENT = "network-infra-transient"
    EXTERNAL_SITE_FLAKE = "external-site-flake"
    UNKNOWN = "unknown"
    NO_SCROLLABLE_CONTENT = "no-scrollable-content"


class ResolutionStatus(StrEnum):
    """Whether *this class* of failure (not this one individual
    occurrence) has a real fix, or is an accepted, documented limit.

    - ``UNRESOLVED``: no fix exists yet, and this isn't being treated as
      a permanent, accepted limit either -- open.
    - ``KNOWN_LIMITATION``: a real, deliberate decision that this class
      of failure is accepted as-is (e.g. AppArmor's sandbox constraint,
      documented and left for a real deploy environment to confirm,
      never "fixed" inside this sandbox) -- matches this project's own
      long-standing "قيد بيئة sandbox: تتسجل وتتسيب لحد VPS" convention.
    - ``RESOLVED``: a real fix landed and was CI-confirmed (this
      project's own standing rule: never marked resolved on a single
      lucky run for a failure already known to be flaky -- see
      docs/REQUIREMENTS.md section 7 entry 6's own multi-run
      confirmation discipline).
    """

    UNRESOLVED = "unresolved"
    KNOWN_LIMITATION = "known-limitation"
    RESOLVED = "resolved"


class FailureRecord(BaseModel):
    """One classified failure, from any source in the project.

    ``target``: the URL, domain, or config name the failure happened
    against -- whichever is most specific and available at the call
    site (a provider-level failure usually has the real URL; a
    circuit-breaker failure only ever sees a domain, since Scrapy's own
    per-request detail isn't available at that layer).

    ``provider``: which :class:`~src.core.interfaces.antibot_provider.AntibotProvider`
    implementation (``"byparr"``/``"camoufox"``/``"patchright"``) was
    involved, ``"playwright"`` for ``PlaywrightMiddleware``'s own,
    separate render path, or ``None`` for a provider-agnostic failure
    (rate limiting, circuit breaker -- both apply before any provider
    is ever selected).

    ``raw_signal``: whatever structured data the original diagnostic
    already had -- copied verbatim, not summarized or lossily
    re-derived, so nothing already-collected (scroll counts, denial
    counts, HTTP status, byparr's own error message, ...) is lost by
    being classified. Deliberately untyped (``dict[str, Any]``) since
    every source shape is genuinely different -- forcing one shared
    schema onto the *raw* data (rather than just the classification
    wrapping it) would mean silently dropping fields no schema author
    anticipated.

    ``source``: which module/event produced this record (e.g.
    ``"circuit_breaker.opened"``, or ``"docs/REQUIREMENTS.md entry 17"``
    for a historical backfill entry) -- necessary provenance the user's
    own schema draft's 5 named fields didn't spell out but every one of
    them implicitly needs (you cannot audit or trust a classification
    without knowing where it came from) -- see
    :mod:`src.diagnostics.failure_registry`'s own module docstring for
    why this is a deliberate, documented addition, not scope creep.
    """

    timestamp: datetime
    target: str
    provider: str | None = None
    failure_category: FailureCategory
    raw_signal: dict[str, Any] = Field(default_factory=dict)
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    source: str
