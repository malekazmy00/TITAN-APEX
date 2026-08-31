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

**Real, independent-reviewer-found bug, fixed here (docs/REQUIREMENTS.md
section 9 entry 21's own reviewer session)**: :func:`record_new_session`'s
own load-modify-save sequence used to be a plain, unlocked
read-modify-write against one shared file -- exactly the kind of thing
this module's own docstring already knew to worry about for retention
correctness, but had not actually protected against for *concurrent
writers*. Reproduced directly by the reviewer (real OS threads, via
Twisted's ``deferToThread``, which is genuinely how every real
``solve()`` call already runs): two concurrent callers each load the
same old state, each append their own new session in memory, and
whichever one saves *last* silently overwrites the other's -- 19 of 20
concurrently-recorded sessions vanished in the reviewer's own
reproduction, with no error and nothing in any log. :func:`record_new_session`
now holds a real, OS-level exclusive lock (``fcntl.flock``, POSIX --
this project only ever targets Linux, the same assumption its own
AppArmor/dmesg diagnostics elsewhere already make, so no new
cross-platform dependency is justified for this) across its *entire*
load+modify+save critical section, via a dedicated lock file
(``<jar_path>.lock``, never the jar file itself, so locking and the
jar's own real content stay conceptually and mechanically separate).
:func:`save_jar` also now writes to a unique temporary file first and
renames it into place (atomic on POSIX) rather than truncating the
real jar file in place -- a real, if lower-severity, secondary risk
the same reviewer flagged (a process killed mid-write could otherwise
corrupt the jar; :func:`load_jar`'s own corruption tolerance was
already a safety net for that, but preventing it outright is strictly
better than merely tolerating it after the fact).

Reads (:func:`load_jar`/:func:`load_accumulated_state`) are
deliberately **not** locked -- the atomic rename above already
guarantees any reader either sees the complete old file or the
complete new one, never a partial write, which is the actual property
a reader needs; adding read-side locking on top would only add
contention with no correctness gain here.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import random
import threading
import time
from collections.abc import Iterator
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

    Writes atomically: the new content goes to a uniquely-named
    temporary file in the same directory first (same filesystem, so the
    rename below is guaranteed atomic on POSIX, not a cross-device
    copy), then :meth:`Path.replace` renames it over ``jar_path`` in one
    atomic step. A reader (:func:`load_jar`) racing this write therefore
    always sees either the complete old file or the complete new one,
    never a truncated/partial one -- this is what actually protects
    concurrent *readers*; concurrent *writers* still need the real
    locking in :func:`record_new_session`, which this function alone
    cannot provide (a rename is atomic, but "read old content, compute
    new content, rename it in" is still a read-modify-write sequence
    with a race across two separate calls to this function).

    The temporary file's name includes both the PID and the current
    thread's identity so that two concurrent writers (which
    :func:`record_new_session`'s own lock already serializes against
    each other in practice, but this function is also callable
    directly/independently) can never collide on the same temp path.

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
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp_path.write_text(json.dumps([s.to_dict() for s in sessions]), encoding="utf-8")
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _locked_jar_file(jar_path: str) -> Iterator[None]:
    """Holds a real, OS-level exclusive lock (``fcntl.flock``) across
    the entire load-modify-save critical section in
    :func:`record_new_session`, using a dedicated ``<jar_path>.lock``
    file -- never the jar file itself, so locking stays mechanically
    separate from the jar's own real content (:func:`save_jar` is free
    to rename a fresh file into ``jar_path`` without disturbing the
    lock file's own identity/fd).

    POSIX-only (``fcntl``) -- an already-established, project-wide
    assumption (this project only ever targets Linux; see e.g. the
    AppArmor/dmesg diagnostics elsewhere in this codebase), not a new
    one introduced here.

    ``fcntl.flock`` locks are process-scoped by *file descriptor*, not
    by path -- so two concurrent callers *within the same process*
    (e.g. two threads via Twisted's ``deferToThread``, the real,
    reproduced scenario this fixes) still serialize correctly against
    each other here, because each holds its own independent ``open()``
    call and thus its own fd on the same underlying lock file; the
    kernel enforces exclusivity across fds/processes, not just across
    processes.
    """
    lock_path = f"{jar_path}.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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

    The entire load-modify-save sequence below runs under a real,
    held-for-the-whole-duration exclusive lock (:func:`_locked_jar_file`)
    -- without it, two concurrent callers (a real, reproduced scenario:
    every ``solve()`` call runs on its own OS thread via Twisted's
    ``deferToThread``, so ``CONCURRENT_REQUESTS_PER_DOMAIN`` >= 2 means
    genuine concurrency here, not just async interleaving) could each
    load the same old sessions, each independently append their own new
    one, and whichever saves last would silently overwrite the other's
    -- a classic lost-update race, previously unguarded, that an
    independent reviewer session reproduced directly (20 concurrent
    recorders, only 1 session survived in the final jar).

    Raises:
        OSError: propagated from :func:`save_jar` -- see its own
            docstring for why this isn't swallowed here either.
    """
    rng = rng if rng is not None else random.Random()
    now = now if now is not None else time.time()
    with _locked_jar_file(jar_path):
        sessions = load_jar(jar_path)
        sessions = apply_rrd_retention(
            [*sessions, JarSession(now, new_storage_state)], rng, now, buckets
        )
        sessions = enforce_max_total_size(sessions, max_total_bytes, now, buckets)
        save_jar(jar_path, sessions)
