"""Unit tests for mock-target/app.py's Flask routes.

Uses Flask's test_client() -- no real network, no real server process --
same spirit as the rest of this project's unit tests never touching a
real network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from app import create_app
from config import MockTargetConfig
from flask.testing import FlaskClient
from parsel import Selector
from security.auth import TEST_PASSWORD, TEST_USERNAME


@pytest.fixture
def config(tmp_path: Path) -> MockTargetConfig:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.feed_rate_limit_threshold = 3
    cfg.feed_rate_limit_window_seconds = 60
    cfg.feed_page_size = 4
    # Every existing test below predates the cookie wall and exercises
    # other layers (posts/decoy/honeypots/feed) independently -- disabled
    # here so they stay exactly as they were; test_cookie_wall_* below
    # builds its own client with it explicitly enabled instead (same "one
    # layer at a time, each verifiable alone" idea config.py documents).
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    return cfg


@pytest.fixture
def client(config: MockTargetConfig) -> FlaskClient:
    app = create_app(config)
    app.testing = True
    return app.test_client()


def test_healthz_returns_ok(client: FlaskClient) -> None:
    """Happy path: the Docker healthcheck endpoint reports ok."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_index_renders_posts_decoy_and_honeypots(client: FlaskClient) -> None:
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.count('data-role="post"') == 11  # 10 real posts + 1 decoy
    assert "honeypot-trap" in body
    assert 'style="display:none"' in body  # the decoy twin


def test_index_sets_a_session_cookie(client: FlaskClient) -> None:
    response = client.get("/")

    assert "mocktarget_session" in response.headers.get("Set-Cookie", "")


def test_api_feed_first_page_has_no_after_param(client: FlaskClient) -> None:
    """Happy path: the API returns a nested edges/comments/page_info shape."""
    response = client.get("/api/feed")
    data = response.get_json()

    assert response.status_code == 200
    assert len(data["edges"]) == 4  # feed_page_size from the fixture config
    first_edge = data["edges"][0]
    assert "post" in first_edge
    assert "comments" in first_edge
    assert data["page_info"]["end_cursor"] == "0"


def test_api_feed_rate_limits_after_the_threshold(client: FlaskClient) -> None:
    """Failure case 1: exceeding the configured threshold returns 429 with a
    real Retry-After header, not a silent pass-through."""
    for _ in range(3):
        client.get("/api/feed")

    response = client.get("/api/feed")

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


def test_api_feed_rejects_a_malformed_cursor(client: FlaskClient) -> None:
    """Failure case 2: a tampered cursor is a 400, not a 500 crash."""
    response = client.get("/api/feed?after=not-a-cursor")

    assert response.status_code == 400


def test_honeypot_trap_logs_and_returns_ok(client: FlaskClient, config: MockTargetConfig) -> None:
    response = client.get("/honeypot-trap/sometoken")

    assert response.status_code == 200
    log_content = Path(config.honeypot_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level"] == "CRITICAL"
    assert payload["token"] == "sometoken"


def test_botd_report_logs_the_posted_result(client: FlaskClient, config: MockTargetConfig) -> None:
    response = client.post("/botd-report", json={"bot": "phantomjs"})

    assert response.status_code == 200
    log_content = Path(config.botd_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level"] == "WARNING"


def test_botd_report_handles_a_missing_body(client: FlaskClient) -> None:
    """Failure-adjacent case 3: a POST with no/invalid JSON body must not 500 --
    treated as an empty report."""
    response = client.post("/botd-report")

    assert response.status_code == 200


def test_fingerprint_report_logs_the_posted_result(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """docs/REQUIREMENTS.md section 9 entry 19: same log-only shape as
    /botd-report above -- confirms the score itself (not just the raw
    report) actually reaches the log line."""
    response = client.post(
        "/fingerprint-report", json={"webglAvailable": False, "viewportConsistent": True}
    )

    assert response.status_code == 200
    log_content = Path(config.fingerprint_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["score"] == 1


def test_fingerprint_report_handles_a_missing_body(client: FlaskClient) -> None:
    """Failure-adjacent case: a POST with no/invalid JSON body must not
    500 -- treated as an empty report (score 0)."""
    response = client.post("/fingerprint-report")

    assert response.status_code == 200


def test_warmup_home_sets_a_session_cookie(client: FlaskClient) -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 1: the real entry
    point of the warm-up chain issues the session cookie Level 2 later
    checks for."""
    response = client.get("/warmup-home")

    assert response.status_code == 200
    assert "mocktarget_warmup_session" in response.headers.get("Set-Cookie", "")


def test_warmup_home_does_not_reissue_an_existing_cookie(client: FlaskClient) -> None:
    """Regression sentinel: silently reissuing the cookie here would make
    every request look "warmed up" regardless of real history -- see
    app.py's own comment on this route for why only /warmup-home ever
    sets it."""
    client.set_cookie("mocktarget_warmup_session", "existing-token")

    response = client.get("/warmup-home")

    assert "Set-Cookie" not in response.headers


def test_warmup_category_logs_a_clean_check_with_a_real_referer_and_cookie(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """Happy path: a real predecessor Referer + an existing warm-up
    cookie scores 0 on both levels."""
    client.set_cookie("mocktarget_warmup_session", "existing-token")

    response = client.get(
        "/warmup-category", headers={"Referer": "http://localhost/warmup-home"}
    )

    assert response.status_code == 200
    log_content = Path(config.referer_session_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["level1_score"] == 0
    assert payload["level2_score"] == 0


def test_warmup_category_flags_a_cold_hit_with_no_referer_or_cookie(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """Failure-adjacent case: hitting a deep page directly, with neither
    a real predecessor Referer nor a warm-up cookie, scores both Level 2
    violations -- exactly what our own scraper would look like today
    without this entry's own GenericSpider warm_session_urls change."""
    response = client.get("/warmup-category")

    assert response.status_code == 200
    log_content = Path(config.referer_session_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level2_score"] == 2


def test_warmup_target_logs_a_clean_check_after_a_real_multi_hop_chain(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """The real, full chain -- not an isolated single-route check: a
    genuine /warmup-home visit (issuing the cookie), then /warmup-target
    with the category page as its Referer, must score 0 on both
    levels."""
    client.get("/warmup-home")

    response = client.get(
        "/warmup-target", headers={"Referer": "http://localhost/warmup-category"}
    )

    assert response.status_code == 200
    log_content = Path(config.referer_session_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["level1_score"] == 0
    assert payload["level2_score"] == 0


def test_warmup_target_renders_the_extractable_item(client: FlaskClient) -> None:
    response = client.get("/warmup-target")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-role="warmup-item"' in body
    assert 'data-item-id="1"' in body


def test_feed_page_renders_the_infinite_scroll_shell(client: FlaskClient) -> None:
    """The /feed page itself ships no posts server-side -- everything comes
    from /api/feed via the scroll listener, same shape as
    quotes.toscrape.com/scroll."""
    response = client.get("/feed")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-role="feed"' in body
    assert "post-item" not in body  # no posts pre-rendered server-side


def test_feed_page_sets_a_session_cookie(client: FlaskClient) -> None:
    response = client.get("/feed")

    assert "mocktarget_session" in response.headers.get("Set-Cookie", "")


def test_feed_page_ships_the_virtualization_config_when_enabled(client: FlaskClient) -> None:
    """docs/REQUIREMENTS.md section 9 entry 13: the default config
    (ENABLE_DOM_VIRTUALIZATION default True) ships the eviction rule
    (virtualizationEnabled/windowSize) client-side -- a Flask test client
    can't execute the script, so this checks the static markers a real
    browser's JS would read, the same shape
    test_placeholder_content_shows_loading_text_with_the_real_text_hidden
    already uses for its own script."""
    body = client.get("/feed").get_data(as_text=True)

    assert "const virtualizationEnabled = true;" in body
    assert "const windowSize = 5;" in body  # the default DOM_VIRTUALIZATION_WINDOW_SIZE
    assert "removeChild" in body


def test_feed_page_ships_the_virtualization_spacer(client: FlaskClient) -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's DOM Virtualization race
    investigation: a real, confirmed bug fix in this mock target's own
    virtualization *fidelity* (structural/dom_virtualization.py's own
    docstring says the intent was mimicking a real virtualized list
    "regardless of how much total content has actually been scrolled
    through" -- removeChild() alone, with nothing compensating for the
    space evicted content used to occupy, never actually delivered
    that). Without a spacer, `document.body`'s own rendered height
    shrinks back down to `windowSize` posts' worth the moment eviction
    starts -- confirmed for real that a genuine, trusted
    page.mouse.wheel() scroll (correct browser-input-level automation)
    then stops producing any 'scroll' event at all, since a real
    browser correctly refuses to fire one once there is no real
    scrollable distance left. The spacer element must ship in the
    markup (its accumulated height is a runtime-only JS concern, not
    checkable from a Flask test client that never executes the
    script -- same limitation this file's other virtualization test
    already documents)."""
    body = client.get("/feed").get_data(as_text=True)

    assert 'data-role="virtualization-spacer"' in body
    assert "spacerHeightPx" in body
    assert "offsetHeight" in body


def test_feed_page_ships_the_progressive_diagnostic_counters(client: FlaskClient) -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's monitoring-infrastructure
    investment: the `container`'s data-load-more-calls/data-load-more-dropped
    attributes must ship on every /feed render, unconditionally (not
    gated behind virtualization/any other toggle) -- camoufox_provider.py/
    patchright_provider.py read these back for real, live diagnostic
    evidence distinguishing "loadMore() actually ran" from "silently
    dropped by the loading guard".

    **Revision (same entry 17, a real bug in the original version of
    this diagnostic, not in this test):** originally plain
    `window.__loadMoreCalls`/`__loadMoreDropped` expando properties --
    confirmed by hand that Camoufox/Firefox's automation protocol
    cannot read a `window.*` property back once it's set by the page's
    own inline `<script>` (`page.evaluate()` sees `undefined` every
    time, silently coerced to a misleadingly plausible `0` by the old
    `|| 0` fallback), even while loadMore() keeps running for real. A
    DOM attribute set by that same inline script does not have this
    problem -- see camoufox_provider.py's own `_read_feed_attr`
    docstring for the full three-control-case confirmation."""
    body = client.get("/feed").get_data(as_text=True)

    assert 'container.setAttribute("data-load-more-calls", "0");' in body
    assert 'container.setAttribute("data-load-more-dropped", "0");' in body
    assert '"data-load-more-dropped",' in body
    assert '"data-load-more-calls",' in body


def test_feed_page_disables_virtualization_when_configured_off(tmp_path: Path) -> None:
    """Failure-adjacent case: disabling the layer must ship
    virtualizationEnabled = false, so the real client-side script never
    evicts anything -- same "one layer at a time, each verifiable alone"
    isolation every other layer's own disabled-case test already has."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_dom_virtualization = False
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/feed").get_data(as_text=True)

    assert "const virtualizationEnabled = false;" in body


def test_feed_page_window_size_is_configurable(tmp_path: Path) -> None:
    """A non-default window size actually reaches the rendered script,
    not silently ignored in favour of the default."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.dom_virtualization_window_size = 3
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/feed").get_data(as_text=True)

    assert "const windowSize = 3;" in body


def test_markup_randomizer_disabled_yields_empty_classes(tmp_path: Path) -> None:
    """Failure-adjacent case 4: disabling the randomizer must not crash
    template rendering -- every logical name still resolves, just to ''."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_markup_randomizer = False
    cfg.enable_cookie_wall = False  # exercising index.html's rendering, not the wall
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer

    app = create_app(cfg)
    app.testing = True
    response = app.test_client().get("/")

    assert response.status_code == 200


def test_layers_can_be_individually_disabled(tmp_path: Path) -> None:
    """Every challenge layer is independently toggleable -- disabling honeypots
    and the decoy must remove them from the rendered page while posts still
    render."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_honeypots = False
    cfg.enable_decoy_data = False
    cfg.enable_botd = False
    cfg.enable_cookie_wall = False  # otherwise every assertion below passes vacuously
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer

    app = create_app(cfg)
    app.testing = True
    body = app.test_client().get("/").get_data(as_text=True)

    assert "honeypot-trap" not in body
    assert 'style="display:none"' not in body
    assert "botd.esm.js" not in body


def _ab_variant_client(tmp_path: Path, rand_fn: object) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False  # isolate the A/B-variant layer alone
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    cfg.enable_markup_randomizer = False  # so the container's class="" is predictable
    app = create_app(cfg, ab_variant_rand_fn=rand_fn)  # type: ignore[arg-type]
    app.testing = True
    return app.test_client()


def test_ab_variant_a_renders_article_containers(tmp_path: Path) -> None:
    """Happy path: a low roll renders the original <article> container,
    with every data-role/content untouched."""
    client = _ab_variant_client(tmp_path, rand_fn=lambda: 0.0)

    body = client.get("/").get_data(as_text=True)

    assert '<article class="" data-role="post"' in body
    assert '<div class="" data-role="post"' not in body
    assert body.count('data-role="post"') == 11  # 10 real posts + 1 decoy, unaffected


def test_ab_variant_b_renders_div_containers_with_the_same_data_roles(tmp_path: Path) -> None:
    """Failure-adjacent case 1: a high roll swaps the container tag to
    <div> -- a tag-qualified selector (`article[data-role="post"]`, this
    project's own mock_target*.yaml configs) would match nothing here,
    while the data-role attributes/content stay identical."""
    client = _ab_variant_client(tmp_path, rand_fn=lambda: 0.99)

    body = client.get("/").get_data(as_text=True)

    assert '<div class="" data-role="post"' in body
    assert '<article class="" data-role="post"' not in body
    assert body.count('data-role="post"') == 11


def test_ab_variant_disabled_always_renders_article(tmp_path: Path) -> None:
    """Failure-adjacent case 2: disabling the layer must fall back to the
    original, stable <article> tag regardless of rand_fn."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    cfg.enable_markup_randomizer = False
    cfg.enable_ab_variants = False
    app = create_app(cfg, ab_variant_rand_fn=lambda: 0.99)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert '<article class="" data-role="post"' in body
    assert '<div class="" data-role="post"' not in body


def test_placeholder_content_shows_loading_text_with_the_real_text_hidden(
    tmp_path: Path,
) -> None:
    """Happy path: the raw HTML shows the literal placeholder, with the
    real text tucked away in data-real-text -- exactly what a scraper
    reading raw HTML (no JS) would capture as the field value."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert ">Loading...</p>" in body
    assert "data-real-text=" in body
    assert "setTimeout" in body
    assert "}, 500);" in body  # the default PLACEHOLDER_DELAY_MS


def test_placeholder_content_disabled_renders_real_text_directly(tmp_path: Path) -> None:
    """Failure-adjacent case 1: disabling the layer must render the real
    text immediately, with no placeholder or swap script at all."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    cfg.enable_placeholder_content = False
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert "Loading..." not in body
    assert "data-real-text=" not in body


def test_placeholder_delay_is_configurable(tmp_path: Path) -> None:
    """Failure-adjacent case 2: a non-default delay actually reaches the
    rendered swap script, not silently ignored in favour of the default."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False  # isolate: existing tests predate this layer
    cfg.placeholder_delay_ms = 2000
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert "}, 2000);" in body


def _cookie_wall_client(tmp_path: Path) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = True
    cfg.enable_shadow_dom = False  # isolate the cookie-wall layer alone
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def test_cookie_wall_blocks_real_content_without_consent(tmp_path: Path) -> None:
    """Happy path (from the wall's own perspective): no consent cookie at
    all means no real posts anywhere in the response -- a genuine
    server-side gate, not a CSS-hidden overlay (structural/cookie_wall.py's
    docstring)."""
    client = _cookie_wall_client(tmp_path)

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-role="cookie-consent-wall"' in body
    assert 'data-role="post"' not in body
    assert "honeypot-trap" not in body


def test_accept_cookies_sets_the_consent_cookie_and_redirects(tmp_path: Path) -> None:
    """Failure-adjacent case 1: following the real Accept link/route must
    both set the consent cookie and redirect back to the real page."""
    client = _cookie_wall_client(tmp_path)

    response = client.get("/accept-cookies")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert "cookie_consent=accepted" in response.headers.get("Set-Cookie", "")


def test_index_shows_real_content_once_consent_cookie_is_present(tmp_path: Path) -> None:
    """Failure-adjacent case 2: a request that already carries the consent
    cookie (i.e. the wall was already passed) sees real content -- the
    wall is a one-time gate, not a permanent block."""
    client = _cookie_wall_client(tmp_path)
    client.set_cookie("cookie_consent", "accepted")

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-role="cookie-consent-wall"' not in body
    assert 'data-role="post"' in body


def _shadow_dom_client(tmp_path: Path) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False  # isolate the shadow-DOM layer alone
    cfg.enable_shadow_dom = True
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def test_shadow_dom_odd_posts_render_as_opaque_placeholders_not_light_dom(
    tmp_path: Path,
) -> None:
    """Happy path: every other post (odd 0-based index) is a
    <mock-shadow-post> placeholder in the raw HTML, carrying none of its
    real content directly (no data-role="post", no author/text/likes
    anywhere as plain text) -- only an opaque base64 payload the
    server-side response never decodes itself. The other half (even
    index) render exactly as they always did."""
    client = _shadow_dom_client(tmp_path)

    body = client.get("/").get_data(as_text=True)

    assert body.count("<mock-shadow-post ") == 5  # odd indices 1,3,5,7,9
    # 5 light-DOM real posts (even indices) + 1 decoy twin (always light DOM)
    assert body.count('data-role="post"') == 6
    assert "data-shadow-payload=" in body


def test_shadow_dom_attach_script_is_present_when_enabled(tmp_path: Path) -> None:
    """The client-side renderer that would build the real shadow roots is
    actually shipped on the page -- without it, the placeholders above
    would never become real content even in a live browser."""
    client = _shadow_dom_client(tmp_path)

    body = client.get("/").get_data(as_text=True)

    assert "attachShadow" in body


def test_shadow_dom_disabled_renders_every_post_in_light_dom(tmp_path: Path) -> None:
    """Failure-adjacent case: disabling the layer must fall back to every
    post rendering directly, same as before this layer existed."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.enable_shadow_dom = False
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert "<mock-shadow-post" not in body
    assert "attachShadow" not in body
    assert body.count('data-role="post"') == 11  # 10 real posts + 1 decoy


# --- Login/session (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Known
# Limitation #1, activated ahead of Interstitials per explicit user
# request) --------------------------------------------------------------


def _auth_client(
    tmp_path: Path, *, protected_feed_total_pages: int = 2, protected_feed_page_size: int = 3
) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False  # isolate the login/session layer alone
    cfg.enable_shadow_dom = False
    cfg.protected_feed_total_pages = protected_feed_total_pages
    cfg.protected_feed_page_size = protected_feed_page_size
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def _extract_csrf_token(login_page_body: str) -> str:
    token = Selector(text=login_page_body).css('input[name="csrf_token"]::attr(value)').get()
    assert token, f"no csrf_token hidden field found in: {login_page_body!r}"
    return token


def _log_in(client: FlaskClient) -> None:
    """Performs a real GET /login -> parse csrf -> POST login sequence,
    the same steps a real crawler must take -- used as setup by tests
    below that only care about what happens *after* a successful login."""
    login_body = client.get("/login").get_data(as_text=True)
    token = _extract_csrf_token(login_body)
    response = client.post(
        "/login",
        data={"csrf_token": token, "username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 302, f"setup login failed: {response.get_data(as_text=True)}"


def test_login_page_serves_a_fresh_csrf_token_every_load(tmp_path: Path) -> None:
    """The real requirement this whole layer exists for: the token
    changes on every GET /login, never fixed/hardcodable."""
    client = _auth_client(tmp_path)

    first_token = _extract_csrf_token(client.get("/login").get_data(as_text=True))
    second_token = _extract_csrf_token(client.get("/login").get_data(as_text=True))

    assert first_token != second_token


def test_login_succeeds_with_valid_credentials_and_token_sets_session_cookie(
    tmp_path: Path,
) -> None:
    """Happy path: a real GET -> parse -> POST sequence with the right
    credentials and the token that page actually issued succeeds,
    redirects to /feed-protected, and sets the auth session cookie."""
    client = _auth_client(tmp_path)
    login_body = client.get("/login").get_data(as_text=True)
    token = _extract_csrf_token(login_body)

    response = client.post(
        "/login",
        data={"csrf_token": token, "username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/feed-protected"
    assert "mocktarget_auth_session=" in response.headers.get("Set-Cookie", "")


def test_login_rejects_wrong_credentials_with_a_valid_token(tmp_path: Path) -> None:
    """Failure-adjacent case 1: a correct, freshly-issued CSRF token does
    not bypass a real credentials check."""
    client = _auth_client(tmp_path)
    token = _extract_csrf_token(client.get("/login").get_data(as_text=True))

    response = client.post(
        "/login", data={"csrf_token": token, "username": TEST_USERNAME, "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid_credentials"}


def test_login_rejects_a_missing_csrf_token(tmp_path: Path) -> None:
    """Failure-adjacent case 2: correct credentials alone are not enough
    without a real token."""
    client = _auth_client(tmp_path)

    response = client.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD})

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid_csrf_token"}


def test_login_rejects_a_reused_csrf_token(tmp_path: Path) -> None:
    """Failure-adjacent case 3: real replay protection -- a token already
    consumed by one successful POST cannot be used again, even with the
    exact same (correct) credentials."""
    client = _auth_client(tmp_path)
    token = _extract_csrf_token(client.get("/login").get_data(as_text=True))
    first = client.post(
        "/login",
        data={"csrf_token": token, "username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert first.status_code == 302  # sanity: the first use really did succeed

    second = client.post(
        "/login",
        data={"csrf_token": token, "username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert second.status_code == 403
    assert second.get_json() == {"error": "invalid_csrf_token"}


def test_feed_protected_rejects_without_a_session_cookie(tmp_path: Path) -> None:
    """The user's own explicit choice: a real, explicit 401 -- not a
    redirect to /login -- for an unauthenticated request."""
    client = _auth_client(tmp_path)

    response = client.get("/feed-protected")

    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}


def test_feed_protected_returns_real_posts_once_logged_in(tmp_path: Path) -> None:
    """Happy path: the exact same data /feed would show, gated behind a
    real session this time."""
    client = _auth_client(tmp_path, protected_feed_page_size=3)
    _log_in(client)

    body = client.get("/feed-protected").get_data(as_text=True)

    assert body.count('data-role="post"') == 3


def test_feed_protected_exposes_a_next_page_link_when_more_pages_remain(tmp_path: Path) -> None:
    client = _auth_client(tmp_path, protected_feed_total_pages=2)
    _log_in(client)

    body = client.get("/feed-protected").get_data(as_text=True)

    assert 'data-role="next-page"' in body
    assert 'href="/feed-protected?page=1"' in body


def test_feed_protected_has_no_next_page_link_on_the_last_page(tmp_path: Path) -> None:
    client = _auth_client(tmp_path, protected_feed_total_pages=2)
    _log_in(client)

    body = client.get("/feed-protected?page=1").get_data(as_text=True)

    assert 'data-role="next-page"' not in body


def test_feed_protected_rejects_a_non_integer_page(tmp_path: Path) -> None:
    client = _auth_client(tmp_path)
    _log_in(client)

    response = client.get("/feed-protected?page=abc")

    assert response.status_code == 400


def test_feed_protected_rejects_a_negative_page(tmp_path: Path) -> None:
    client = _auth_client(tmp_path)
    _log_in(client)

    response = client.get("/feed-protected?page=-1")

    assert response.status_code == 400


def test_test_expire_session_invalidates_the_callers_own_session(tmp_path: Path) -> None:
    """The deterministic, non-time-based hook a live test uses to trigger
    session-expiry *detection* -- see security/auth.py's
    SessionStore.force_expire docstring for why this exists at all."""
    client = _auth_client(tmp_path)
    _log_in(client)
    assert client.get("/feed-protected").status_code == 200  # sanity: really was valid

    expire_response = client.get("/test-expire-session")
    assert expire_response.get_json() == {"status": "expired"}

    assert client.get("/feed-protected").status_code == 401


def test_test_expire_session_without_a_session_reports_no_session(tmp_path: Path) -> None:
    client = _auth_client(tmp_path)

    response = client.get("/test-expire-session")

    assert response.get_json() == {"status": "no_session"}


# --- Interstitials (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's محور
# 6) -----------------------------------------------------------------


def _interstitial_client(
    tmp_path: Path,
    *,
    trigger: str = "time",
    delay_ms: int = 1000,
    scroll_percent: int = 30,
    page_size: int = 3,
    total_batches: int = 2,
) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False  # isolate the interstitial layer alone
    cfg.enable_shadow_dom = False
    cfg.interstitial_trigger = trigger
    cfg.interstitial_delay_ms = delay_ms
    cfg.interstitial_scroll_percent = scroll_percent
    cfg.interstitial_feed_page_size = page_size
    cfg.interstitial_feed_total_batches = total_batches
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def test_feed_interstitial_page_ships_the_overlay_markup_hidden_by_default(
    tmp_path: Path,
) -> None:
    """The overlay itself is real markup shipped in the initial response
    -- not injected only by JS -- starting hidden (display:none), shown
    later by the trigger script. Real content stays in a separate,
    always-present container, never wrapped by the overlay."""
    client = _interstitial_client(tmp_path)

    body = client.get("/feed-interstitial").get_data(as_text=True)

    assert 'data-role="interstitial"' in body
    assert 'data-role="interstitial-close"' in body
    assert "display:none" in body
    assert 'data-role="feed"' in body


def test_feed_interstitial_page_wires_the_configured_trigger(tmp_path: Path) -> None:
    """The trigger script actually reflects this app's own config, not a
    hardcoded default -- a scroll-mode client gets scroll-trigger JS, a
    time-mode client gets a setTimeout instead."""
    time_client = _interstitial_client(tmp_path, trigger="time", delay_ms=1234)
    scroll_client = _interstitial_client(tmp_path, trigger="scroll", scroll_percent=42)

    time_body = time_client.get("/feed-interstitial").get_data(as_text=True)
    scroll_body = scroll_client.get("/feed-interstitial").get_data(as_text=True)

    assert "setTimeout(showInterstitial, 1234);" in time_body
    assert "setTimeout(showInterstitial, 1234);" not in scroll_body
    assert "pct >= 42" in scroll_body
    assert "pct >= 42" not in time_body


def test_feed_interstitial_page_sets_a_session_cookie(tmp_path: Path) -> None:
    client = _interstitial_client(tmp_path)

    response = client.get("/feed-interstitial")

    assert "mocktarget_session" in response.headers.get("Set-Cookie", "")


def test_api_feed_interstitial_first_batch_has_no_after_param(tmp_path: Path) -> None:
    client = _interstitial_client(tmp_path, page_size=3, total_batches=2)

    response = client.get("/api/feed-interstitial")

    body = response.get_json()
    assert len(body["edges"]) == 3
    assert body["page_info"]["end_cursor"] == "0"
    assert body["page_info"]["has_next_page"] is True


def test_api_feed_interstitial_last_batch_has_no_next_page(tmp_path: Path) -> None:
    client = _interstitial_client(tmp_path, page_size=3, total_batches=2)

    first = client.get("/api/feed-interstitial").get_json()
    second = client.get(
        "/api/feed-interstitial?after=" + first["page_info"]["end_cursor"]
    ).get_json()

    assert len(second["edges"]) == 3
    assert second["page_info"]["has_next_page"] is False


def test_api_feed_interstitial_rejects_a_malformed_cursor(tmp_path: Path) -> None:
    client = _interstitial_client(tmp_path)

    response = client.get("/api/feed-interstitial?after=not-a-page")

    assert response.status_code == 400


# --- JA4 fingerprint logging (docs/REQUIREMENTS.md section 9 entry 17,
# claude/ja4-experiment branch) -----------------------------------------


def test_ja4_header_present_is_logged_on_any_route(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """The real header the JA4 proxy sets (test-environment/ja4-proxy/
    haproxy.cfg) is logged, on any route -- this is a global
    before_request hook, not something wired into individual routes
    one at a time. /healthz is used here specifically because it's the
    simplest possible route, proving this isn't route-specific logic."""
    response = client.get("/healthz", headers={"X-JA4-Fingerprint": "t13d1516h2_abc_def"})

    assert response.status_code == 200
    log_content = Path(config.ja4_log_path).read_text(encoding="utf-8")
    payload = json.loads(log_content.strip().splitlines()[-1])
    assert payload["ja4_fingerprint"] == "t13d1516h2_abc_def"


def test_ja4_header_absent_logs_nothing(client: FlaskClient, config: MockTargetConfig) -> None:
    """Every existing route, reached the normal way (no JA4 proxy in
    front) -- the log file must not even gain a line for this, matching
    security/ja4_integration.py's own "silent by default" docstring."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert not Path(config.ja4_log_path).read_text(encoding="utf-8").strip()


# --- /spa-catalog (docs/REQUIREMENTS.md section 9 entry 23, Phase 2
# بند 6, Known Limitation #5's real fix) ---------------------------------


def test_spa_catalog_renders_the_hydration_skeleton_only(client: FlaskClient) -> None:
    """The initial server-rendered HTML ships no real product markup at
    all -- everything comes from the client-side hydration script, same
    shape /feed's own "no posts pre-rendered server-side" test above."""
    response = client.get("/spa-catalog")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="spa-catalog-grid"' in body
    assert "Loading products" in body
    assert "<img" not in body  # no product images pre-rendered server-side


def test_spa_catalog_sets_a_session_cookie(client: FlaskClient) -> None:
    response = client.get("/spa-catalog")

    assert "mocktarget_session" in response.headers.get("Set-Cookie", "")


def test_spa_catalog_ships_the_hydration_delay_and_product_payload(
    client: FlaskClient, config: MockTargetConfig
) -> None:
    """The real delay and the full catalog data must be present in the
    page for the client-side script to use -- a Flask test client can't
    execute the script itself, so this checks the static markers a real
    browser's JS would read (same limitation the /feed virtualization
    tests above already document)."""
    config.spa_catalog_product_count = 5
    body = client.get("/spa-catalog").get_data(as_text=True)

    assert f"setTimeout(renderCatalog, {config.spa_hydration_delay_ms});" in body
    assert body.count('"product_id"') == 5


def test_spa_catalog_ships_opaque_non_semantic_class_names(client: FlaskClient) -> None:
    """docs/REQUIREMENTS.md section 9 entry 23: the whole point of this
    challenge -- every scraped field's class name is an opaque token
    (MarkupRandomizer's own ``x{8 hex digits}`` shape), never a
    semantic word a config could plausibly hardcode as a stable
    selector."""
    body = client.get("/spa-catalog").get_data(as_text=True)

    match = re.search(r'image:\s*"([^"]+)"', body)
    assert match is not None
    image_class = match.group(1)
    assert re.fullmatch(r"x[0-9a-f]{8}", image_class)
    assert image_class not in ("image", "spa-image", "product-image")


def test_spa_catalog_product_count_is_configurable(tmp_path: Path) -> None:
    """Failure-adjacent case: the catalog size actually reflects config,
    not a hardcoded constant."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.ja4_log_path = str(tmp_path / "ja4.log")
    cfg.fingerprint_log_path = str(tmp_path / "fingerprint.log")
    cfg.referer_session_log_path = str(tmp_path / "referer_session.log")
    cfg.enable_cookie_wall = False
    cfg.spa_catalog_product_count = 3
    app = create_app(cfg)
    app.testing = True

    body = app.test_client().get("/spa-catalog").get_data(as_text=True)

    assert body.count('"product_id"') == 3
