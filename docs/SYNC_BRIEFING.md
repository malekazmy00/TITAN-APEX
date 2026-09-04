# Sync Briefing

Append-only, per-session handoff log. Each entry is written at the end
of a work session so a *different* session (or a different person) can
resume without re-discovering anything already settled here. Never
edit a past entry -- if something it says turns out wrong, a later
entry corrects it explicitly (same append-only discipline as
`docs/REQUIREMENTS.md`).

Read this file first when picking up work on this branch. Read
`docs/REQUIREMENTS.md` for the full evidence trail behind anything
summarized here -- this file is an index/status board, not a
replacement for it.

---

## 2026-09-04 -- entries 31/32/32.1 closed, all CI-confirmed

**Branch**: `claude/osint-scraping-platform-wnuyk6`, HEAD at this
entry's own commit. No new branch was created this session (see
"pending decisions" below for why).

### Done, CI-confirmed with real run IDs

- **Entry 31 -- Cumulative Session Trust Score** (Phase 3 item 1):
  `test-environment/mock-target/structural/trust_score.py`,
  `/trust-scored` endpoint, escalating RATE_LIMITED -> CHALLENGE ->
  BLOCKED. CI run
  [33805946963](https://github.com/malekazmy00/TITAN-APEX/actions/runs/33805946963)
  (commit `db9d9e2`), confirmed again in run
  [33869062672](https://github.com/malekazmy00/TITAN-APEX/actions/runs/33869062672).
- **Entry 32 -- JA4 task correction**: the user's request to "fully
  implement JA4 TLS Fingerprinting" was built on a wrong premise --
  Steps A/B/C were already merged (entry 18) and Step D was already
  run with a *conclusive* finding (entry 19, not an environment
  constraint): Camoufox's TLS/JA4 fingerprint is byte-for-byte
  identical to real Firefox's (primary source: daijro/camoufox issue
  #555). Re-verified live today with a new test
  (`tests/integration/test_ja4_fingerprint_matches_real_firefox_live.py`)
  against this project's own existing ja4-proxy pipeline -- captured
  value matched the historical one **byte-for-byte**
  (`t13d1617h2_86a278354501_3cbfd9057e0d`, 7/7 requests). CI run
  [33869062672](https://github.com/malekazmy00/TITAN-APEX/actions/runs/33869062672)
  (commit `73aebbf`). **JA4-based classification is permanently
  abandoned** -- the pipeline itself (ja4-proxy, mock-target logging)
  stays wired and log-only, harmless, but nothing builds on it.
- **Entry 32.1 -- Cross-Signal Consistency** (Phase 3 item 2,
  redefined): the agreed replacement for JA4 -- combines BotD +
  fpscanner + request-timing regularity (three signals that already
  existed independently) into one `inconsistency_score` via
  `POST /cross-signal-check`, log-only.
  `security/cross_signal_consistency.py` (new). CI run
  [33873737246](https://github.com/malekazmy00/TITAN-APEX/actions/runs/33873737246)
  (commit `4353409`) -- **attempt 1 failed for a real but unrelated
  reason** (see below), **attempt 2 (rerun) green**: 274/274
  test-environment, 58/58 integration.

### A real, unrelated CI failure hit and resolved this session

`camoufox fetch`'s own call to `api.github.com/repos/camoufox/
camoufox/releases` hit a genuine `403 rate limit exceeded` during CI
run 33873737246's first attempt -- the "Fetch Camoufox browser binary"
step was marked `success` by GitHub Actions despite silently fetching
zero versions (worth knowing: that step does not fail loudly on a
sync failure, only later tests that actually try to launch Camoufox
do). Confirmed via the step's own raw log (not the 2000-line job-log
tail, which gets cut before this early step -- had to download the
full logs zip via `get_workflow_run_logs_url` to see it). Zero overlap
with the diff being tested. One `rerun_failed_jobs` resolved it
cleanly. **First occurrence of this specific failure mode in this
project's history** -- not yet a recurring pattern (the project's own
threshold for `failure_taxonomy` registration is 3+ occurrences,
entry 30.2's own precedent), so not formally registered, just
documented here and in entry 32.1 section 7. If it recurs, register it.

### Environment constraints discovered this session (real, verified directly)

- **No Docker daemon in this sandbox.** `docker ps` fails with
  "Cannot connect to the Docker daemon". `docker-compose.test.yml` /
  the full mock-target+Anubis+ja4-proxy+Byparr stack can only be
  verified through real CI in this environment, never locally. Local
  verification for Flask-only changes (no browser/Docker needed) is
  still possible by running `mock-target/app.py` directly
  (`python3 -c "from app import create_app; create_app().run(...)"`)
  -- used successfully this session for both entry 31 and entry 32.1's
  pre-push sanity checks. **Known gotcha**: running it this way from
  inside `mock-target/` resolves the config's relative log paths
  against the wrong cwd and creates a stray nested
  `mock-target/test-environment/...` directory -- always `rm -rf` it
  before committing (hit and cleaned up twice this session, entries
  31 and 32.1 both document it).
- **No plain/vanilla Firefox binary anywhere in this toolchain** --
  neither this sandbox nor `ci.yml` itself (`playwright install` only
  ever fetches `chromium`; `camoufox fetch` fetches Camoufox's own
  patched build, not stock Firefox). A true live A/B between Camoufox
  and real Firefox is not achievable with this project's own tooling
  today -- confirmed directly (`sync_playwright().firefox.launch()`
  fails with "Executable doesn't exist"), not assumed. Entry 32's own
  re-verification worked around this by comparing against a
  historical value from the same pipeline instead of a live second
  browser.
- **`camoufox fetch`'s own GitHub API calls can be rate-limited** in
  CI (see above) -- a real, if so-far-single-occurrence, transient
  failure mode worth recognizing on sight in future CI investigations
  rather than re-diagnosing from scratch.

### Pending decisions / open items for the user

- **No new branch was created** for the JA4 task despite the original
  request asking for one (`claude/ja4-full-implementation`). Once the
  actual scope turned out to be a small live re-check + a redefinition
  of Phase 3 item 2 (not a large, isolated infra build), continuing on
  the existing designated branch
  (`claude/osint-scraping-platform-wnuyk6`, per this session's own
  system-level branch instructions) seemed like the better call than
  branch proliferation for contained work -- flagged here explicitly
  as a deviation from the literal request, not a silent one.
- **Phase 3's full scope beyond items 1 and 2 is not documented
  anywhere in this repo.** Entries 28/29 refer to their own
  diagnostics work as "Phase 3 بند 3" in passing, but there is no
  single place enumerating a Phase 3 item list -- "Phase 3 item 1/2"
  in entries 31/32.1 are the user's own live-session naming, not a
  pre-existing roadmap document. If there's a Phase 3 item 3+ in mind,
  it needs to be stated explicitly next time, the same way item 1 and
  item 2 were.
- The stale-looking task-tracker entry `#17 [in_progress] DOM
  Virtualization Instability` (visible in this harness's own task
  list) does **not** reflect reality -- `docs/REQUIREMENTS.md` entry
  17 documents it as closed and statistically confirmed (15 parallel
  CI runs) back before entry 18. This is a harness task-list/doc
  mismatch, not an open item -- worth a manual `TaskUpdate` to mark it
  completed next session, not something to re-investigate.
