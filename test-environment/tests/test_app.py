"""Unit tests for mock-target/app.py's Flask routes.

Uses Flask's test_client() -- no real network, no real server process --
same spirit as the rest of this project's unit tests never touching a
real network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app import create_app
from config import MockTargetConfig
from flask.testing import FlaskClient


@pytest.fixture
def config(tmp_path: Path) -> MockTargetConfig:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.feed_rate_limit_threshold = 3
    cfg.feed_rate_limit_window_seconds = 60
    cfg.feed_page_size = 4
    # Every existing test below predates the cookie wall and exercises
    # other layers (posts/decoy/honeypots/feed) independently -- disabled
    # here so they stay exactly as they were; test_cookie_wall_* below
    # builds its own client with it explicitly enabled instead (same "one
    # layer at a time, each verifiable alone" idea config.py documents).
    cfg.enable_cookie_wall = False
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


def test_markup_randomizer_disabled_yields_empty_classes(tmp_path: Path) -> None:
    """Failure-adjacent case 4: disabling the randomizer must not crash
    template rendering -- every logical name still resolves, just to ''."""
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.enable_markup_randomizer = False
    cfg.enable_cookie_wall = False  # exercising index.html's rendering, not the wall

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
    cfg.enable_honeypots = False
    cfg.enable_decoy_data = False
    cfg.enable_botd = False
    cfg.enable_cookie_wall = False  # otherwise every assertion below passes vacuously

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
    cfg.enable_cookie_wall = False  # isolate the A/B-variant layer alone
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
    cfg.enable_cookie_wall = False
    cfg.enable_markup_randomizer = False
    cfg.enable_ab_variants = False
    app = create_app(cfg, ab_variant_rand_fn=lambda: 0.99)
    app.testing = True

    body = app.test_client().get("/").get_data(as_text=True)

    assert '<article class="" data-role="post"' in body
    assert '<div class="" data-role="post"' not in body


def _cookie_wall_client(tmp_path: Path) -> FlaskClient:
    cfg = MockTargetConfig()
    cfg.honeypot_log_path = str(tmp_path / "honeypot.log")
    cfg.botd_log_path = str(tmp_path / "botd.log")
    cfg.enable_cookie_wall = True
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
