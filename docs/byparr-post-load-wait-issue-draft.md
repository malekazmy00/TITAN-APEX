<!--
Draft for a GitHub issue on github.com/ThePhaseless/Byparr.

Not filed automatically: this session's GitHub access is scoped to
malekazmy00's repositories only (confirmed via add_repo -- cross-owner
repo access isn't supported mid-session for this account tier). Submit
this manually, or ask for a session/task scoped to that repo as its
initial source.

Delete this file (or move it out of the repo) once the issue is filed --
it's a submission draft, not project documentation.
-->

# Title

Browser closes at `load` event, before challenges that finish async work after `load` (e.g. Anubis's real proof-of-work flow)

# Body

## What happened

Solving a page protected by [Anubis](https://github.com/TecharoHQ/anubis) (a real proof-of-work reverse proxy) via `POST /v1` (`cmd: "request.get"`) reliably returns Anubis's *challenge* page, not the real content behind it — even though Anubis genuinely issues a challenge to Byparr's request (confirmed in Anubis's own server log: `"msg":"new challenge issued"`, weight-based threshold matched for Byparr's browser-like User-Agent).

## Root cause (confirmed by hand, reproducible)

Anubis's real challenge flow computes a small proof-of-work *asynchronously in the browser, after the page's `load` event fires* — a JS routine that then POSTs the result to `/.within.website/x/cmd/anubis/api/pass-challenge`, gets a real auth cookie back, and reloads.

Watching Anubis's own request log for 20+ seconds after a `POST /v1` call to Byparr had already returned showed **no further activity at all** — no `pass-challenge` call, no proxied request, nothing. That matches Byparr's own log line for the request (`navigating to "...", waiting until "load"`): Byparr's browser is torn down as soon as `load` fires, before Anubis's async, post-load challenge JS ever gets a chance to run.

This isn't specific to Anubis — any challenge/anti-bot flow that does real work *after* the page's `load` event (rather than blocking `load` itself, or being visible via a DOM/network signal Byparr already polls for) will hit the same gap.

## Suggested fix

An optional parameter on `POST /v1` (e.g. `postLoadWaitMs` or similar) that holds the browser open for a fixed extra duration *after* `load` fires, before capturing the response and closing the browser — the same shape FlareSolverr-family tools and Playwright/Camoufox's own `page.wait_for_timeout()` already support at the page level. A sensible default of `0` (current behavior, no behavior change for existing callers) with an opt-in wait would cover this without touching the default fast path.

## What we did in the meantime

We didn't wait on this — we added a second `AntibotProvider` implementation in our own project that drives a browser directly (via [Camoufox](https://github.com/daijro/camoufox)) instead of going through Byparr's API, specifically so we can hold the browser open past `load` ourselves. Byparr is still our default/primary provider for everything else; this is the one specific gap that pushed us to add an alternative for challenges shaped like Anubis's.

Happy to share more detail (exact Anubis policy config, full logs) if useful for reproducing this.
