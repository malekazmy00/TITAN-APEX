"""Unit tests for src/providers/antibot/cookie_jar_manager.py.

Every function here is pure (or file-I/O only, via tmp_path) -- no real
browser, no real wall-clock time, no real randomness needed: `rng`/`now`
are always injected explicitly, the same "no real browser/time/
randomness needed to test" pattern this project already established for
_scroll.py's own randomized_scroll_delta/randomized_pause_ms.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from src.providers.antibot.cookie_jar_manager import (
    DEFAULT_RETENTION_BUCKETS,
    JarSession,
    RetentionBucket,
    apply_rrd_retention,
    enforce_max_total_size,
    load_accumulated_state,
    load_jar,
    merge_sessions_into_storage_state,
    record_new_session,
    save_jar,
)

DAY = 86_400


def _cookie(name: str, value: str, domain: str = "example.com", path: str = "/") -> dict[str, str]:
    return {"name": name, "value": value, "domain": domain, "path": path}


def _session(age_seconds: float, now: float, cookie_name: str = "c") -> JarSession:
    return JarSession(
        timestamp=now - age_seconds,
        storage_state={"cookies": [_cookie(cookie_name, f"v-{age_seconds}")], "origins": []},
    )


# --- apply_rrd_retention -------------------------------------------------


def test_full_resolution_bucket_keeps_every_session() -> None:
    """The most recent week is full resolution -- nothing is dropped or
    sampled, no matter how many sessions fall inside it."""
    now = 1_000_000.0
    sessions = [_session(age, now) for age in (0, 3600, 2 * DAY, 6 * DAY)]

    kept = apply_rrd_retention(sessions, random.Random(0), now)

    assert len(kept) == 4


def test_downsampled_bucket_keeps_at_most_one_per_slot() -> None:
    """Three sessions all landing in the same daily slot of the
    "last month" bucket must collapse to exactly one -- the real point
    of RRD-style consolidation."""
    now = 1_000_000.0
    # All three ~10 days old, well within the same 86400s-wide slot.
    sessions = [_session(10 * DAY, now, "a"), _session(10 * DAY + 100, now, "b"),
                _session(10 * DAY + 200, now, "c")]

    kept = apply_rrd_retention(sessions, random.Random(0), now)

    assert len(kept) == 1


def test_downsampled_bucket_keeps_one_per_distinct_slot() -> None:
    """Two sessions in *different* daily slots both survive -- sampling
    is per-slot, not a single global cap on the whole bucket."""
    now = 1_000_000.0
    sessions = [_session(10 * DAY, now, "a"), _session(11 * DAY, now, "b")]

    kept = apply_rrd_retention(sessions, random.Random(0), now)

    assert len(kept) == 2


def test_retention_choice_within_a_slot_is_randomized_not_always_the_newest() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21 Step 2: jittered, not a
    suspiciously regular "always keep the newest" rule -- confirmed by
    hand here: two different seeds pick two different real candidates
    from the same slot."""
    now = 1_000_000.0
    sessions = [_session(10 * DAY, now, "a"), _session(10 * DAY + 50, now, "b")]

    kept_seed_a = apply_rrd_retention(sessions, random.Random(1), now)
    kept_seed_b = apply_rrd_retention(sessions, random.Random(7), now)

    assert len(kept_seed_a) == 1
    assert len(kept_seed_b) == 1
    # Not asserting they *always* differ (a seed pair could coincidentally
    # agree) -- asserting the mechanism is a real rng.choice, not a fixed
    # "max(timestamp)"/"min(timestamp)" rule, by checking across several
    # seeds at least one disagreement shows up.
    picks = {
        apply_rrd_retention(sessions, random.Random(seed), now)[0].storage_state["cookies"][0][
            "value"
        ]
        for seed in range(20)
    }
    assert len(picks) > 1, "expected rng.choice to vary the kept session across seeds"


def test_sessions_older_than_every_bucket_are_dropped_entirely() -> None:
    now = 1_000_000.0
    ancient = _session(800 * DAY, now)  # older than the 730-day final bucket

    kept = apply_rrd_retention([ancient], random.Random(0), now)

    assert kept == []


def test_apply_rrd_retention_with_no_sessions() -> None:
    assert apply_rrd_retention([], random.Random(0), 0.0) == []


# --- enforce_max_total_size -----------------------------------------------


def test_enforce_max_total_size_is_a_noop_when_already_under_the_cap() -> None:
    now = 1_000_000.0
    sessions = [_session(0, now)]

    kept = enforce_max_total_size(sessions, max_total_bytes=10_000, now=now)

    assert kept == sessions


def test_enforce_max_total_size_evicts_the_oldest_lowest_resolution_bucket_first() -> None:
    """A recent (full-resolution) session must survive even when an
    older, coarser-bucket session has to be evicted to fit the cap --
    the real RRD "drop the coarsest, oldest data first" behavior."""
    now = 1_000_000.0
    recent = _session(0, now, "recent")
    ancient_but_in_range = _session(700 * DAY, now, "ancient")
    sessions = [recent, ancient_but_in_range]
    # A cap tight enough to force exactly one eviction, but not so tight
    # it can't hold a single session.
    cap = len(json.dumps([recent.to_dict()]).encode("utf-8")) + 10

    kept = enforce_max_total_size(sessions, max_total_bytes=cap, now=now)

    assert kept == [recent]


def test_enforce_max_total_size_evicts_everything_when_the_cap_is_impossibly_small() -> None:
    """A cap smaller than even one session's own serialized size can
    never be satisfied by keeping anything -- correctly ends up empty
    rather than silently violating the caller's own explicit cap; must
    return, not raise or loop forever."""
    now = 1_000_000.0
    sessions = [_session(0, now)]

    kept = enforce_max_total_size(sessions, max_total_bytes=1, now=now)

    assert kept == []


# --- merge_sessions_into_storage_state --------------------------------------


def test_merge_returns_empty_state_for_no_sessions() -> None:
    assert merge_sessions_into_storage_state([]) == {"cookies": [], "origins": []}


def test_merge_combines_cookies_from_multiple_sessions() -> None:
    session_a = JarSession(1.0, {"cookies": [_cookie("a", "1")], "origins": []})
    session_b = JarSession(2.0, {"cookies": [_cookie("b", "2")], "origins": []})

    merged = merge_sessions_into_storage_state([session_a, session_b])

    names = {c["name"] for c in merged["cookies"]}
    assert names == {"a", "b"}


def test_merge_prefers_the_chronologically_later_session_on_conflict() -> None:
    """Same (name, domain, path) cookie in two sessions -- the real,
    most-recent value wins, regardless of list order."""
    older = JarSession(1.0, {"cookies": [_cookie("c", "old-value")], "origins": []})
    newer = JarSession(2.0, {"cookies": [_cookie("c", "new-value")], "origins": []})

    merged = merge_sessions_into_storage_state([newer, older])  # deliberately out of order

    assert merged["cookies"] == [_cookie("c", "new-value")]


def test_merge_combines_origins_and_prefers_the_later_session() -> None:
    older = JarSession(
        1.0, {"cookies": [], "origins": [{"origin": "https://a.example", "localStorage": [1]}]}
    )
    newer = JarSession(
        2.0, {"cookies": [], "origins": [{"origin": "https://a.example", "localStorage": [2]}]}
    )

    merged = merge_sessions_into_storage_state([older, newer])

    assert merged["origins"] == [{"origin": "https://a.example", "localStorage": [2]}]


# --- load_jar / save_jar ----------------------------------------------------


def test_load_jar_returns_empty_list_for_a_missing_file(tmp_path: Path) -> None:
    assert load_jar(str(tmp_path / "does-not-exist.json")) == []


def test_load_jar_returns_empty_list_for_corrupted_json(tmp_path: Path) -> None:
    path = tmp_path / "jar.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert load_jar(str(path)) == []


def test_load_jar_returns_empty_list_when_top_level_is_not_a_list(tmp_path: Path) -> None:
    path = tmp_path / "jar.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    assert load_jar(str(path)) == []


def test_load_jar_skips_malformed_entries_but_keeps_valid_ones(tmp_path: Path) -> None:
    path = tmp_path / "jar.json"
    path.write_text(
        json.dumps([{"timestamp": 1.0, "storage_state": {}}, {"missing": "fields"}]),
        encoding="utf-8",
    )

    sessions = load_jar(str(path))

    assert len(sessions) == 1
    assert sessions[0].timestamp == 1.0


def test_save_jar_then_load_jar_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "jar.json"
    sessions = [JarSession(1.0, {"cookies": [_cookie("a", "1")], "origins": []})]

    save_jar(str(path), sessions)
    loaded = load_jar(str(path))

    assert loaded == sessions


# --- load_accumulated_state --------------------------------------------------


def test_load_accumulated_state_is_none_for_an_empty_jar(tmp_path: Path) -> None:
    assert load_accumulated_state(str(tmp_path / "empty.json")) is None


def test_load_accumulated_state_returns_the_merged_state(tmp_path: Path) -> None:
    path = tmp_path / "jar.json"
    save_jar(str(path), [JarSession(1.0, {"cookies": [_cookie("a", "1")], "origins": []})])

    state = load_accumulated_state(str(path))

    assert state == {"cookies": [_cookie("a", "1")], "origins": []}


# --- record_new_session (the real, full round trip) -------------------------


def test_record_new_session_creates_a_fresh_jar_and_is_loadable(tmp_path: Path) -> None:
    path = tmp_path / "jar.json"

    record_new_session(
        str(path), {"cookies": [_cookie("a", "1")], "origins": []},
        rng=random.Random(0), now=1_000_000.0,
    )

    state = load_accumulated_state(str(path))
    assert state == {"cookies": [_cookie("a", "1")], "origins": []}


def test_record_new_session_accumulates_across_multiple_calls(tmp_path: Path) -> None:
    """The real point of the whole module: a second, separate call sees
    what the first one saved, merged with its own new cookie."""
    path = tmp_path / "jar.json"
    record_new_session(
        str(path), {"cookies": [_cookie("a", "1")], "origins": []},
        rng=random.Random(0), now=1_000_000.0,
    )

    record_new_session(
        str(path), {"cookies": [_cookie("b", "2")], "origins": []},
        rng=random.Random(0), now=1_000_001.0,
    )

    state = load_accumulated_state(str(path))
    assert state is not None
    assert {c["name"] for c in state["cookies"]} == {"a", "b"}


def test_record_new_session_applies_retention_and_size_cap(tmp_path: Path) -> None:
    """A real, end-to-end proof that record_new_session doesn't just
    append forever -- an ancient session recorded first is gone once
    retention pruning has a chance to run on a later call far enough in
    the future."""
    path = tmp_path / "jar.json"
    record_new_session(
        str(path), {"cookies": [_cookie("old", "1")], "origins": []},
        rng=random.Random(0), now=0.0,
    )

    # Two years and a day later -- the first session is now older than
    # every retention bucket's own max_age_seconds.
    record_new_session(
        str(path), {"cookies": [_cookie("new", "2")], "origins": []},
        rng=random.Random(0), now=731 * DAY,
    )

    state = load_accumulated_state(str(path))
    assert state is not None
    assert {c["name"] for c in state["cookies"]} == {"new"}


def test_default_retention_buckets_are_in_increasing_age_order() -> None:
    """Regression sentinel: apply_rrd_retention's own bucket-lookup logic
    assumes buckets are sorted ascending by max_age_seconds."""
    ages = [b.max_age_seconds for b in DEFAULT_RETENTION_BUCKETS]
    assert ages == sorted(ages)


def test_retention_bucket_is_a_real_frozen_dataclass() -> None:
    """Regression sentinel: callers rely on RetentionBucket instances
    being hashable/immutable (used as dict values, compared by value)."""
    bucket = RetentionBucket(max_age_seconds=1.0, sample_interval_seconds=2.0)
    with pytest.raises(AttributeError):
        bucket.max_age_seconds = 5.0  # type: ignore[misc]
