"""Cumulative, cross-session cookie jar -- docs/REQUIREMENTS.md section 9
entry 21 Step 2 (the second, deferred half of item 10's Referer/session
-warmup work: a *long-lived* accumulated browser profile, spanning many
separate real crawl runs over real time, as opposed to Step 1's
GenericSpider-level warm-up chain, which only ever spans one crawl's own
requests).

**What this solves, and what it deliberately doesn't:** a brand-new
browser context (what every ``CamoufoxProvider``/``PatchrightProvider``
``solve()`` call has always created, one per call, with zero
continuity) always looks exactly like what it is -- a profile that has
never visited anything before. A real, long-lived human browser profile
instead carries cookies from *many* unrelated sites, accumulated over
weeks or months. This module is the mechanism that makes a fresh
context, for a *new* ``solve()`` call, start from that accumulated
state instead of empty -- via Playwright's own real, documented
``browser.new_context(storage_state=...)``/``context.storage_state()``
round trip (docs/REQUIREMENTS.md section 9 entry 21's own verification:
confirmed directly, both against Playwright's official docs and by hand
against a real Camoufox session -- save, then reload in a completely
separate browser instance, and the cookie survives, including a
session cookie with no explicit expiry). **Real, documented correction
recorded in the same entry**: a cited GitHub issue
(microsoft/playwright#36139) that looked at first like a
``storage_state()`` limitation for session cookies specifically turned
out, on direct reading, to document a *different* API's real limitation
(``launch_persistent_context``/``user_data_dir``) -- its own reporter
uses ``storage_state()`` as the *working fix* for exactly this problem.
This module is built on that corrected, verified understanding.

**Why "organic, unrelated-site cookies" needs no special code at all**:
a real requirement from this entry's own design discussion was that a
stored session shouldn't be 100% cookies from one single target -- a
real long-lived profile has cookies from everywhere it's ever been.
This module is deliberately *not* target-scoped at all: one jar file,
shared across every real ``solve()`` call this whole project ever
makes, for every target. Since this project already crawls many
distinct targets (test-environment/mock-target, quotes.toscrape.com,
Hacker News, ...), a shared jar naturally accumulates a genuine mix of
unrelated-site cookies purely as a structural consequence of normal
operation -- exactly the "organically collected during normal work"
framing this entry's own design discussion used, not something that
needs to be synthesized or faked.

**RRD (Round Robin Database) -- style retention, not unbounded growth**:
running for real, indefinitely, an append-only jar would grow forever.
Confirmed directly against RRDtool's own canonical documentation
(https://oss.oetiker.ch/rrdtool/doc/rrdcreate.en.html, the actual origin
of this pattern's name): recent data is kept at full resolution, older
data is progressively consolidated into coarser buckets, and the total
size is bounded, with the oldest/lowest-resolution data evicted first.
:data:`DEFAULT_RETENTION_BUCKETS` is this module's own real translation
of that idea to *sessions* rather than numeric metric samples (there is
no meaningful "average" of two cookie sets the way there is for a CPU
load average -- the natural equivalent RRDtool's own CF concept maps to
here is *sampling*, keeping one representative session per time slot
instead of averaging): the most recent week keeps every session
untouched; the following month keeps roughly one per day; the six
months after that, roughly one per week; the two years after that,
roughly one per month; anything older is dropped entirely. Real,
independently confirmed research (docs/REQUIREMENTS.md section 9 entry
21) found the Python ecosystem for this *specific* pattern thin --
``rrdtool``'s own Python bindings need the real C library installed,
``pyrrd`` has been abandoned since 2011, ``whisper`` is pure Python but
tightly coupled to Graphite's own conventions and hasn't shipped a
release in years, and ``tsdownsample`` (the one actively-maintained
candidate) solves a different problem (display-time downsampling of an
already-in-memory series) and adds a compiled Rust dependency. Hand-
rolling this small, pure-Python implementation was the recommended,
and is the actual, choice.

**Jittered sampling within a bucket, not evenly-spaced picks**: the
same "an unnaturally regular generation pattern is itself a detectable
signature" principle this project already applied to mouse-movement
simulation (docs/REQUIREMENTS.md section 9 entry 20) applies here too
-- real human browsing has no clean weekly/monthly cadence (some weeks
have many sessions, some have none, e.g. a real vacation). Retaining
"the newest session in each time slot" every single time would itself
be a suspiciously regular selection rule; :func:`apply_rrd_retention`
picks uniformly at random among a slot's real candidates instead.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JarSession:
    """One real ``solve()`` call's own ``context.storage_state()``
    snapshot, tagged with when it happened (a Unix timestamp, injectable
    for deterministic tests -- see this module's own functions)."""

    timestamp: float
    storage_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "storage_state": self.storage_state}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> JarSession:
        return JarSession(timestamp=data["timestamp"], storage_state=data["storage_state"])


@dataclass(frozen=True)
class RetentionBucket:
    """One RRD-style age tier: any session whose age (``now - timestamp``)
    is at most ``max_age_seconds`` and greater than the previous bucket's
    own ``max_age_seconds`` belongs here. ``sample_interval_seconds`` is
    the target spacing between kept sessions in this tier -- ``0`` means
    full resolution (keep every session, no sampling at all)."""

    max_age_seconds: float
    sample_interval_seconds: float


#: docs/REQUIREMENTS.md section 9 entry 21 Step 2's own real retention
#: schedule -- see this module's own docstring for the full reasoning:
#: last week untouched, then progressively coarser (daily / weekly /
#: monthly) sampling out to two years, nothing older kept at all.
DEFAULT_RETENTION_BUCKETS: tuple[RetentionBucket, ...] = (
    RetentionBucket(max_age_seconds=7 * 86_400, sample_interval_seconds=0),
    RetentionBucket(max_age_seconds=30 * 86_400, sample_interval_seconds=86_400),
    RetentionBucket(max_age_seconds=180 * 86_400, sample_interval_seconds=7 * 86_400),
    RetentionBucket(max_age_seconds=730 * 86_400, sample_interval_seconds=30 * 86_400),
)

#: A real, explicit ceiling on the jar file's own serialized size --
#: docs/REQUIREMENTS.md section 9 entry 21 Step 2's own requirement:
#: "a real explicit maximum" is not optional. 5 MiB comfortably holds
#: many hundreds of real sessions (cookies/localStorage are small) while
#: staying a trivial read/write cost on every single solve() call.
DEFAULT_MAX_JAR_BYTES = 5 * 1024 * 1024


def _bucket_index_for_age(age_seconds: float, buckets: tuple[RetentionBucket, ...]) -> int | None:
    """Which bucket (by index into ``buckets``) a session of this age
    belongs to -- ``None`` if it's older than every bucket's own
    ``max_age_seconds`` (too old to keep at all)."""
    for index, bucket in enumerate(buckets):
        if age_seconds <= bucket.max_age_seconds:
            return index
    return None


def apply_rrd_retention(
    sessions: list[JarSession],
    rng: random.Random,
    now: float,
    buckets: tuple[RetentionBucket, ...] = DEFAULT_RETENTION_BUCKETS,
) -> list[JarSession]:
    """The real RRD-style consolidation step: groups ``sessions`` by age
    bucket, keeps every session in a full-resolution (``sample_interval_
    seconds == 0``) bucket untouched, and for every other bucket keeps
    at most one *randomly chosen* (this module's own docstring explains
    why randomly, not "always the newest") session per
    ``sample_interval_seconds``-wide time slot within that bucket. A
    session older than every bucket's own ``max_age_seconds`` is dropped
    entirely -- outside the whole retention window.

    Pure and deterministic given ``rng``/``now`` -- no real clock or
    randomness needed to test this.
    """
    by_bucket: dict[int, list[JarSession]] = {}
    for session in sessions:
        age = now - session.timestamp
        index = _bucket_index_for_age(age, buckets)
        if index is None:
            continue
        by_bucket.setdefault(index, []).append(session)

    kept: list[JarSession] = []
    for index, bucket in enumerate(buckets):
        bucket_sessions = by_bucket.get(index, [])
        if bucket.sample_interval_seconds <= 0:
            kept.extend(bucket_sessions)
            continue
        by_slot: dict[int, list[JarSession]] = {}
        for session in bucket_sessions:
            age = now - session.timestamp
            slot = int(age // bucket.sample_interval_seconds)
            by_slot.setdefault(slot, []).append(session)
        for slot_sessions in by_slot.values():
            kept.append(rng.choice(slot_sessions))
    return kept


def _serialized_size(sessions: list[JarSession]) -> int:
    return len(json.dumps([s.to_dict() for s in sessions]).encode("utf-8"))


def enforce_max_total_size(
    sessions: list[JarSession],
    max_total_bytes: int,
    now: float,
    buckets: tuple[RetentionBucket, ...] = DEFAULT_RETENTION_BUCKETS,
) -> list[JarSession]:
    """docs/REQUIREMENTS.md section 9 entry 21 Step 2's own explicit size
    cap: if ``sessions`` still serializes larger than ``max_total_bytes``
    after :func:`apply_rrd_retention` already ran, evicts sessions one at
    a time from the *oldest, lowest-resolution* non-empty bucket first
    (the real RRD "drop the coarsest, oldest data first" behavior) until
    it fits, or nothing is left to evict.
    """
    sessions = list(sessions)
    # Oldest/lowest-resolution bucket first -- the last entry in
    # `buckets` covers the largest max_age_seconds.
    for index in range(len(buckets) - 1, -1, -1):
        while _serialized_size(sessions) > max_total_bytes:
            candidates = [
                s for s in sessions if _bucket_index_for_age(now - s.timestamp, buckets) == index
            ]
            if not candidates:
                break
            oldest = min(candidates, key=lambda s: s.timestamp)
            sessions.remove(oldest)
    return sessions


def merge_sessions_into_storage_state(sessions: list[JarSession]) -> dict[str, Any]:
    """Merges every kept session's own cookies/``origins`` (localStorage)
    into one real, active ``storage_state`` dict -- a real browser only
    ever has *one* active cookie jar, never a list of separate per-visit
    snapshots (a session's own recorded timestamp only matters for RRD
    retention, not for what gets handed to
    ``browser.new_context(storage_state=...)``). On any conflicting
    cookie (same ``name``/``domain``/``path``) or ``origins`` entry
    (same ``origin``) across sessions, the value from the
    *chronologically later* session wins -- the real, most-recent value
    that cookie actually had.
    """
    ordered = sorted(sessions, key=lambda s: s.timestamp)
    cookies_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    origins_by_url: dict[str, dict[str, Any]] = {}
    for session in ordered:
        for cookie in session.storage_state.get("cookies", []):
            key = (cookie["name"], cookie["domain"], cookie["path"])
            cookies_by_key[key] = cookie
        for origin in session.storage_state.get("origins", []):
            origins_by_url[origin["origin"]] = origin
    return {"cookies": list(cookies_by_key.values()), "origins": list(origins_by_url.values())}


def load_jar(jar_path: str) -> list[JarSession]:
    """Reads every session currently stored in ``jar_path``.

    Never raises: a missing, unreadable, or corrupted jar file is
    exactly equivalent to "no accumulated profile exists yet" -- the
    same real, no-assumption default this whole feature already has
    for a target that's never been solved before, not a crawl-ending
    failure.
    """
    path = Path(jar_path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    sessions: list[JarSession] = []
    for entry in raw:
        try:
            sessions.append(JarSession.from_dict(entry))
        except (KeyError, TypeError):
            continue
    return sessions


def save_jar(jar_path: str, sessions: list[JarSession]) -> None:
    """Writes ``sessions`` to ``jar_path``, creating parent directories
    as needed.

    Raises:
        OSError: if ``jar_path``'s parent directory cannot be created,
            or the file cannot be written (e.g. a permissions problem)
            -- propagated, not swallowed, so a caller that cares (a real
            provider's own solve() path) can decide whether to log and
            continue or let it surface; this function itself has no
            business making that call silently.
    """
    path = Path(jar_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.to_dict() for s in sessions]), encoding="utf-8")


def load_accumulated_state(jar_path: str) -> dict[str, Any] | None:
    """What a provider passes straight to
    ``browser.new_context(storage_state=...)`` -- the real, merged,
    currently-active cookie jar (:func:`merge_sessions_into_storage_state`),
    or ``None`` if ``jar_path`` has no sessions at all yet (a fresh
    profile -- identical to never passing ``storage_state`` at all,
    Playwright's own real default).
    """
    sessions = load_jar(jar_path)
    if not sessions:
        return None
    return merge_sessions_into_storage_state(sessions)


def record_new_session(
    jar_path: str,
    new_storage_state: dict[str, Any],
    rng: random.Random | None = None,
    now: float | None = None,
    buckets: tuple[RetentionBucket, ...] = DEFAULT_RETENTION_BUCKETS,
    max_total_bytes: int = DEFAULT_MAX_JAR_BYTES,
) -> None:
    """The one real entry point a provider calls after a successful
    ``solve()``: appends ``new_storage_state`` (that call's own
    ``context.storage_state()``) as a fresh session, applies the RRD
    retention schedule (:func:`apply_rrd_retention`) and the absolute
    size cap (:func:`enforce_max_total_size`), then writes the result
    back via :func:`save_jar`.

    ``rng``/``now`` default to genuine per-call randomness/real wall-
    clock time for every real caller -- only this module's own tests
    inject fixed ones.

    Raises:
        OSError: propagated from :func:`save_jar` -- see its own
            docstring for why this isn't swallowed here either.
    """
    rng = rng if rng is not None else random.Random()
    now = now if now is not None else time.time()
    sessions = load_jar(jar_path)
    sessions = apply_rrd_retention(
        [*sessions, JarSession(now, new_storage_state)], rng, now, buckets
    )
    sessions = enforce_max_total_size(sessions, max_total_bytes, now, buckets)
    save_jar(jar_path, sessions)
