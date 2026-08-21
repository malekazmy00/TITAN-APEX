# Test Environment

A self-hosted, Docker-based adversarial target for TITAN-APEX's own
`GenericSpider` to scrape — permanent project infrastructure (not a
throwaway experiment), built to the same config-driven, interfaces-first
principle as `src/`, and designed to grow: every layer below was added one
at a time, and the same pattern extends to any future layer.

Governed by `docs/REQUIREMENTS.md` section 8 ("Escalation Cycle"): every
round here is one *known* difficulty level, never a claim that the code is
"done". See `test-environment/CHANGELOG.md` for the escalation history.

```
test-environment/
├── mock-target/          # the Flask app under test (routes, templates, challenge layers)
│   ├── structural/       # section 2: markup randomizer, honeypots, decoy data, feed
│   └── security/         # section 1: BotD reporting + file loggers for both
├── anubis/                # section 1.2: Anubis's real official default policy
├── tests/                 # unit tests for every mock-target module (100% coverage)
├── docker-compose.test.yml
└── CHANGELOG.md
```

## Running it locally

```bash
cd test-environment
docker compose -f docker-compose.test.yml up -d --build
curl -s http://localhost:8080/ -o /dev/null -w '%{http_code}\n'   # through Anubis
docker compose -f docker-compose.test.yml down -v
```

`ANUBIS_PORT` (default `8080`) is the only port published to the host —
`mock-target` itself is reachable only from inside the isolated
`test-environment` Docker network, never directly. See "Isolation" below.

## Section 1 — Security layers

Each layer sits in front of `mock-target` as a chain (SafeLine → Anubis →
mock-target, when SafeLine is present) or is embedded in the page itself
(BotD). Every layer documented here follows the same three points: **what
it detects**, **how to verify it's actually active** (not just present in
the compose file), and **its official source**.

### 1.1 SafeLine WAF — investigated, excluded this round

**Decision:** not included in `docker-compose.test.yml` for this
escalation round. This is a documented exclusion, not a silent omission.

**Why (real evidence, not assumption):** SafeLine's own official
`compose.yaml` (chaitin/SafeLine, fetched and read directly, not
guessed) requires:
- `network_mode: host` on its core containers — incompatible with this
  project's mandatory Docker-network isolation (section 4 below: no
  container here may have a route to the public internet, and
  `host` networking defeats that for any container that uses it).
- A multi-container stack of its own (management API + Postgres +
  detector + tengine), each with real startup/health dependencies —
  significant weight and startup time for a CI job budgeted in minutes.
- A first-run setup wizard that in every documented deployment path is
  driven through its web UI — no evidence of a scriptable/headless
  first-run bootstrap suitable for an unattended CI step.

None of that is fatal for local, manual use (a developer can still run
SafeLine standalone outside this compose project if they want to test
against it by hand), but it fails the same bar Byparr and Redis already
clear in `.github/workflows/ci.yml`: a real `services:`-style component
that starts unattended and is provably active every CI run. Revisiting
this is a legitimate future escalation step (section 8's Escalation
Cycle, step 4) if a lighter-weight WAF or a scriptable SafeLine bootstrap
appears later — tracked, not forgotten.

**Source:** https://github.com/chaitin/SafeLine

### 1.2 Anubis — proof-of-work challenge

**What it detects/blocks:** nothing about the *client* directly — for
clients its policy decides to challenge, it makes the request solve a
small proof-of-work puzzle (a JavaScript computation) before it ever
reaches `mock-target`. This is the same shape as real-world PoW gates
(e.g. Cloudflare's own "I'm under attack" mode) — cheap for one real
visitor, expensive at bot scale.

**Real, confirmed behavior — not what the name alone suggests.** The
shipped default policy is *not* "challenge everyone without JS". It is
weight-based (`anubis/botPolicy.yaml`'s `thresholds:` section), and
confirmed directly against this stack:
- A bare `curl` (no browser-like User-Agent) matches no rule at all →
  weight `0` → the `minimal-suspicion` threshold → **ALLOW**, straight
  through to the real page, no challenge at all.
- Scrapy's own default User-Agent (`Scrapy/2.18.0 (+https://scrapy.org)`)
  is **explicitly denied outright** by the shipped `bot/ai-catchall`
  deny list — a well-behaved, self-identifying crawler is exactly what
  that list targets.
- A browser-like User-Agent (matching `Mozilla|Opera` — real browsers,
  Playwright, and Byparr's Chromium all qualify) gets weight `+10` →
  the `moderate-suspicion` threshold → a real **PoW challenge page**.
- **Fixed (round 2, docs/REQUIREMENTS.md section 9):** the browser-UA
  challenge path above is now genuinely reachable and completable up to
  a point. Two real, separate gaps blocked it before:
  - Byparr ran as a GitHub Actions `services:` container on a different
    Docker network than this compose stack, so it could never even
    reach `localhost:8080` at all (`NS_ERROR_CONNECTION_REFUSED`).
    **Fixed:** `docker-compose.test.yml` now runs its own dedicated
    `byparr` service with `network_mode: "service:anubis"` — it shares
    Anubis's network namespace directly, so `localhost:8080` inside
    Byparr's own container genuinely is Anubis.
  - Anubis's challenge-verification cookies default to `Secure` (and
    CHIPS-`Partitioned`), which no real browser persists over plain
    `http://`. **Fixed:** `COOKIE_SECURE=false` and
    `COOKIE_PARTITIONED=false` — both real, documented Anubis flags
    (verified against `cmd/anubis/main.go`), the officially supported
    way to run it without TLS (Anubis has no built-in HTTPS server at
    all), not a workaround.
  - **A third gap surfaced only once both of the above were real,
    still open:** Byparr's browser now genuinely reaches Anubis and
    gets issued a real challenge every time, but its `/v1` call returns
    (tearing the browser context down) as soon as the page's `load`
    event fires — before Anubis's async, post-load PoW-solving JS ever
    gets to run. Confirmed by hand: watching Anubis's own request log
    for 20+ seconds after a completed `solve()` call shows nothing
    further happens at all. See
    `tests/integration/test_mock_target_live.py`'s module docstring for
    the full writeup.

**Policy:** `anubis/botPolicy.yaml` is Anubis's own real, official
*default* `botPolicies.yaml` (MIT-licensed, copied verbatim — see the
provenance comment at the top of the file), not a hand-rolled rule set.

**Required env vars (all already set in `docker-compose.test.yml`):**
`USE_REMOTE_ADDRESS=true` — without it, Anubis 500s on *every* request
(`"[misconfiguration] X-Real-Ip header is not set"`) — confirmed by
hand — because its default expects a real reverse proxy in front of it
to set that header, and there isn't one here. `COOKIE_SECURE=false` and
`COOKIE_PARTITIONED=false` — see the fixed-gaps bullet above.

**How to verify it's actually active (not just present in the compose
file):** compare the three real outcomes above for the same URL —
```bash
curl -s -o /dev/null -w 'bare curl: %{http_code}\n' http://localhost:8080/
curl -s -o /dev/null -w 'Scrapy UA: %{http_code}\n' -A "Scrapy/2.18.0 (+https://scrapy.org)" http://localhost:8080/
curl -s -o /dev/null -w 'browser UA: %{http_code}\n' -A "Mozilla/5.0" http://localhost:8080/
```
A bare `curl` returns `200` with real post content. The Scrapy UA also
returns HTTP `200` at the transport level, but is a **deny**, not a
pass-through — confirmed by Anubis's own request log line
(`"msg":"explicit deny","check_result":{"name":"bot/ai-catchall","rule":"DENY"}`),
not the status code, which is why checking the actual response body
content (not just the HTTP status) is the reliable way to tell "denied"
apart from "allowed" here. The browser UA returns `200` with a real
**challenge** page (title "Making sure you're not a bot!"), not real
content, and Anubis logs `"msg":"new challenge issued"` for it. All
three differing (verified against both body content and Anubis's own
logs, not status code alone) is the real activity check — the container
being "up" is not proof by itself.

**Source:** https://github.com/TecharoHQ/anubis

### 1.3 BotD — client-side bot detection

**What it detects:** browser/automation fingerprint signals (headless
browser tells, automation flags like `navigator.webdriver`, inconsistent
plugin/permission behavior, etc.) — entirely client-side, running in the
page itself. **Deliberately non-blocking**: detection is only ever
logged, never used to deny access. The goal here is not to stop our own
scraper — it's to observe whether *our own* rendering (Playwright via
`PlaywrightMiddleware`, or Byparr's browser) exposes itself as automated,
which is useful signal for stealth improvements regardless of whether
this specific mock target ever enforces on it.

**Where it runs:** `static/vendor/botd.esm.js` is a real, vendored
(not CDN-loaded — see section 4's isolation rule) copy of FingerprintJS's
BotD v2.0.0 ESM build. `templates/index.html` loads it as a page
`<script type="module">` when `ENABLE_BOTD=true`, runs `Botd.detect()`,
and POSTs the raw result to `/botd-report`.

**How to verify it's actually active:**
1. `docker compose ... exec mock-target cat /app/security/botd_flags.log`
   (or wherever `BOTD_LOG_PATH` points) after visiting `/` with a real
   browser — one JSON line per report should appear, `"bot": true` or
   `"bot": false` depending on the client, **regardless of what it says**
   (that's the point: it never blocks).
2. Setting `ENABLE_BOTD=false` and re-visiting `/` must render the page
   with no `<script type="module">` block at all (view-source) and
   produce zero new log lines — proof the toggle actually gates the
   layer, not just the intent to.

**Source:** https://github.com/fingerprintjs/BotD (MIT)

### 1.4 Verifying "active", generally

For any layer above, "present in `docker-compose.test.yml`" is not the
same claim as "active" — the compose file only proves intent. The
verification method documented per-layer is what makes the difference
provable: a request that should be blocked/challenged/logged and
actually is, versus the same request with the layer disabled behaving
differently. Every layer's toggle (section 3) exists specifically so
that comparison can be made one layer at a time.

## Section 2 — Structural challenges

### 2.1 Markup Randomizer

Rotates the CSS **class** (never the `data-role` attribute — see
"Selector guidance" below) of a curated subset of elements
(`app.py`'s `RANDOMIZED_LOGICAL_NAMES`: post/comment item, author, text,
likes) every `MARKUP_RANDOMIZER_INTERVAL_MINUTES` (default 15). Only
*some* elements rotate, not the whole page — layout/nav markup stays
static, the same shape a real site's CSS-modules/styled-components
hashing has (see `docs/REQUIREMENTS.md` section 7 entry 5's real
`react-shopping-cart` investigation, the direct inspiration for this
layer).

**Selector guidance (the actual point of this challenge):** every
templated element carries a stable `data-role` (and `data-post-id` /
`data-comment-id`) attribute *alongside* its rotating class. A selector
built on `[data-role="post"]` survives rotation; one built on the class
name does not. `docs/spiders/configs/mock_target.yaml` uses
`data-role` selectors for exactly this reason — see section 5 below for
the real result.

**Verify it's active:** fetch `/` twice with more than
`MARKUP_RANDOMIZER_INTERVAL_MINUTES` between requests (or set the env
var low, e.g. `1`, for a fast manual check) and diff the `class="..."`
attribute on the same logical element — it must differ; `data-role`
must not.

**If our scraper ever breaks because of this:** logged in
`test-environment/CHANGELOG.md`, with the fix, per section 3's format.

### 2.2 Honeypots

`structural/honeypots.py` generates 4 hidden trap links on `/`, cycling
through all four real-world hiding techniques: `display:none`,
`visibility:hidden`, `opacity:0` + off-screen absolute positioning, and
`aria-hidden="true"`. Each points at a unique `/honeypot-trap/<token>`
URL. **A real human never sees or clicks any of them.**

**What a hit means:** `GenericSpider.parse()` extracts from every CSS
match with no visibility check at all — it is architecturally unable to
tell a hidden element from a visible one. This is a direct, deliberate
test of that gap (see `docs/REQUIREMENTS.md` section 7 for the same
kind of documented architectural limitation). A request reaching
`/honeypot-trap/<token>` is logged **CRITICAL** to
`security/honeypot_triggers.log` immediately — never silently.

**Verify it's active:** `curl -s http://mock-target:8000/honeypot-trap/test123`
from inside the network (or through Anubis) and check the log file gets
a new CRITICAL line with that token.

### 2.3 Poisoned / Decoy Data

`structural/decoy_data.py` builds a stale twin of the first real post —
same `post_id`/`author`, different `text`/`likes`, zero comments — and
`templates/index.html` renders it **first in the DOM**, hidden
(`display:none` + `aria-hidden="true"`). A selector that grabs "the
first match" instead of checking visibility silently returns stale data
instead of the real, currently-visible post. This is a data-quality bug
class, distinct from the honeypot's access-pattern class: nothing here
looks "wrong" in a scraper's output (it's still a well-formed post), it's
just the wrong one.

**Verify it's active:** `curl -s http://mock-target:8000/` (bypassing
Anubis from inside the network) and confirm two `article[data-role="post"]`
elements share the same `data-post-id`, one with `style="display:none"`.

### 2.4 `/feed` — complex dynamic structure (social-media pattern)

The largest structural challenge here, deliberately a different shape
from every config-driven target built so far:

- **Real infinite scroll**: `/feed`'s initial HTML has zero posts. JS
  fetches `/api/feed` only once the viewport is actually near the
  bottom of the page (`window.innerHeight + scrollY >= body height -
  200`) — nothing is present to scrape until a real scroll happens,
  the same shape `PlaywrightMiddleware`'s scroll-to-bottom loop was
  already proven against on `webscraper.io/test-sites/scroll`
  (`docs/TEST_TARGETS.md`), now combined with a JSON API instead of
  server-rendered HTML.
- **Semi-GraphQL `/api/feed`**: one endpoint, `?after=<cursor>` paging,
  returning nested `post → comments → replies` JSON in one response
  instead of separate pages per level — nothing in `GenericSpider`
  today parses JSON API responses; it is CSS-selector-only.
- **Per-session content variance**: the feed's post order/content is
  seeded from a per-session cookie (`mocktarget_session`), so two
  different sessions genuinely see different content — the same shape
  a real ranked feed has, and a real test of whether a scraper wrongly
  assumes a fixed, stable order across runs.
- **Escalating rate limiting**: `structural/feed.py`'s
  `FeedRateLimiter` returns `429` with a `Retry-After` header that
  **grows** with repeated violations in the same sliding window
  (`retry_after_seconds = window_seconds * violations`), instead of an
  immediate hard block — the same pattern real social platforms use.

`/feed` is intentionally **not** wired into
`src/spiders/configs/mock_target.yaml` this round — `GenericSpider` has
no JSON/GraphQL-style parsing path at all today (a much larger
architectural gap than `render_wait_ms`/`click_selector` closed
earlier), so pointing it at `/feed` would only reconfirm a gap that's
already obvious by inspection, not produce new evidence. `/api/feed` is
still fully live and independently testable (`curl`, or a future
dedicated JSON-aware code path) — see `docs/REQUIREMENTS.md` for
whether/when this becomes a tracked "Known Spider Limitation".

### 2.5 Cookie-consent wall

`structural/cookie_wall.py` + `templates/cookie_wall.html`: `/` returns
**only** a consent-gate page (an "Accept" link, no post content anywhere
in the response body at all) whenever the request has no
`cookie_consent=accepted` cookie yet. Following the real link
(`GET /accept-cookies`) sets the cookie and redirects back to `/`, which
now renders the real feed as normal.

Deliberately a server-side gate, not a CSS-hidden overlay: a banner
sitting on top of content that's already present in the DOM would be
trivially defeated by any selector-based scraper that never checks
visibility at all — the same gap 2.2 Honeypots already documents — and
would teach nothing new
(`docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md`'s own point: real
consent walls often gate content this way, not cosmetically).

`GenericSpider`/the antibot providers gained real click support for this
round: `click_selector` (already an existing config field) now reaches
`AntibotProvider.solve()` too, not just `PlaywrightMiddleware` — see
`docs/REQUIREMENTS.md`'s write-up on which provider(s) actually needed it
to get past this wall for real.

**Verify it's active:** `curl -s http://mock-target:8000/` (bypassing
Anubis from inside the network, no cookie jar) and confirm the response
contains `data-role="cookie-consent-wall"` and **no**
`data-role="post"` at all; `curl -s -c - http://mock-target:8000/accept-cookies`
followed by a request replaying that cookie shows real posts instead.

## Section 3 — Extensibility

Every security layer and structural challenge is independently
enable/disable-able via environment variables (`test-environment/.env.test.example`
lists every one with its default), so each layer's effect can be
isolated and tested alone — not just all-on together:

| Variable | Default | Layer |
|---|---|---|
| `ENABLE_BOTD` | `true` | 1.3 BotD |
| `ENABLE_HONEYPOTS` | `true` | 2.2 Honeypots |
| `ENABLE_DECOY_DATA` | `true` | 2.3 Decoy data |
| `ENABLE_MARKUP_RANDOMIZER` | `true` | 2.1 Markup randomizer |
| `ENABLE_COOKIE_WALL` | `true` | 2.5 Cookie-consent wall |
| `MARKUP_RANDOMIZER_INTERVAL_MINUTES` | `15` | 2.1 rotation interval |
| `FEED_RATE_LIMIT_THRESHOLD` | `20` | 2.4 requests/window before 429 |
| `FEED_RATE_LIMIT_WINDOW_SECONDS` | `60` | 2.4 sliding window size |
| `FEED_PAGE_SIZE` | `10` | 2.4 posts per `/api/feed` page |
| `ANUBIS_PORT` | `8080` | 1.2 published host port |

Anubis itself has no `ENABLE_ANUBIS` toggle — it is a separate
container in `docker-compose.test.yml`, included or excluded by
commenting it out (or, going forward, by `docker compose --profile`,
should more optional containers join later), rather than an in-app flag.

`test-environment/CHANGELOG.md` starts empty; every escalation round
(section 8's Escalation Cycle) is logged there.

### How to add a new challenge

The pattern every layer above followed, and the one any future layer
(e.g. simulating an interactive challenge like Kasada's) should follow
too:

1. **One layer, one round.** Add exactly one new security layer or
   structural challenge per escalation round (`docs/REQUIREMENTS.md`
   section 8, step 4) — never several at once, so a resulting spider
   failure has one unambiguous cause.
2. **Real, not simulated.** Prefer a real open-source implementation of
   the technique (Anubis, BotD) over hand-rolling an approximation.
   When no suitable open-source component exists, hand-build the
   minimum needed to reproduce the *real* mechanism (e.g. `honeypots.py`,
   `markup_randomizer.py`) — never a fake stand-in that doesn't actually
   exercise the gap it's meant to test.
3. **A `.env` toggle from the start.** New layers get an
   `ENABLE_<NAME>` (or a numeric tuning var, same naming shape as the
   table above) from their first commit, not retrofitted later.
4. **Logged, not silently blocking, unless the point is to block.**
   Detection-only layers (BotD) log and never block. Layers whose whole
   point is to gate access (Anubis, honeypot triggers) block/log as
   appropriate — but either way, every meaningful event is logged
   somewhere real (`security/*.log` or the layer's own container logs),
   never silently dropped.
5. **Documented here before it's "done".** A new entry under section 1
   or 2 above, with what it detects, how to verify it's really active,
   and its source — matching the exact three-point shape every existing
   entry follows.
6. **The same strict rules as always** (`docs/REQUIREMENTS.md` section
   3 / section 6 below): unit tests (happy path + ≥2 failure cases),
   zero bare `except`, resources closed in `finally`, `ruff`/
   `mypy --strict` clean, coverage intact.
7. **Run for real against `generic_spider.py`**, document what actually
   happened (section 5's pattern), and log the round in
   `test-environment/CHANGELOG.md`.

## Section 4 — Security and isolation

See `docs/ARCHITECTURE.md`, "Test Environment Security" section, for
the full write-up (network isolation, 100% fake data, per-container
resource limits). Summary:

- Two networks, not one: `test-environment` (`internal: true` — no
  container on it has any route to the public internet, and, confirmed
  by hand, is not even reachable *from the host* — Docker doesn't wire
  up port publishing for an internal network at all) and `edge` (a
  normal bridge network, used only so Anubis's port can be published).
  `mock-target` — the thing actually serving fake data, honeypots, and
  decoy data — is on `test-environment` only: no route out, no route in
  except through Anubis. Anubis is on both, the same shape a real
  reverse proxy has (one leg on the protected backend, one leg facing
  the world) — it does have normal outbound access on its `edge` leg,
  same as any real edge proxy; `mock-target` never does.
- Every byte of content (`content_generator.py`) is Faker-generated,
  seeded per session — zero real data from any real source, ever.
- Every service in `docker-compose.test.yml` has an explicit
  `deploy.resources.limits` (CPU and memory) — a runaway bug in one
  container cannot starve the host or the rest of the stack.

## Section 5 — Integration with `src/`

`src/spiders/configs/mock_target.yaml` points `GenericSpider` at this
stack (through Anubis, `antibot_needed: true`, `data-role`-based
selectors) and `tests/integration/test_mock_target_live.py` runs it for
real against the live `docker compose` stack in CI. See that test file
and `docs/REQUIREMENTS.md`'s "Known Gaps from Test Environment" section
(added once this round's real result is in) for the actual outcome —
not assumed, not summarized away.
