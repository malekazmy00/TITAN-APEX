"""Mock scraping-training target: Flask app tying every challenge layer
together. See test-environment/README.md for what each route/layer is
for and how to verify it's actually active.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from config import MockTargetConfig, get_config
from content_generator import generate_catalog, generate_feed_page
from flask import Flask, Response, jsonify, redirect, render_template, request
from security.auth import (
    AUTH_SESSION_COOKIE_NAME,
    CSRF_FIELD_NAME,
    PASSWORD_FIELD_NAME,
    USERNAME_FIELD_NAME,
    CsrfTokenStore,
    SessionStore,
    check_credentials,
)
from security.botd_integration import VENDORED_SCRIPT_PATH, log_botd_report
from security.file_logger import get_file_logger
from security.fpscanner_integration import log_fingerprint_report
from security.honeypot_logger import log_honeypot_trigger
from security.ja4_integration import JA4_HEADER_NAME, log_ja4_fingerprint
from security.referer_session_integration import (
    WARMUP_SESSION_COOKIE_NAME,
    log_referer_session_check,
)
from structural.ab_variant import choose_variant, container_tag_for
from structural.cookie_wall import (
    ACCEPT_PATH,
    CONSENT_COOKIE_NAME,
    CONSENT_COOKIE_VALUE,
    has_consent,
)
from structural.decoy_data import generate_decoy_twin
from structural.feed import FeedRateLimiter, build_feed_page
from structural.honeypots import generate_honeypot_links
from structural.interstitial import build_interstitial_feed_page, render_interstitial_script
from structural.markup_randomizer import MarkupRandomizer
from structural.placeholder_content import PLACEHOLDER_TEXT, render_swap_script
from structural.shadow_dom import SHADOW_ATTACH_SCRIPT, encode_shadow_payload, is_shadow_wrapped
from structural.spa_catalog import HYDRATION_SKELETON_TEXT, products_to_payload

INDEX_PAGE_SIZE = 10
SESSION_COOKIE_NAME = "mocktarget_session"

# The only element classes that rotate -- layout/nav classes stay static,
# same "rotate some things, not everything" shape a real site would use.
RANDOMIZED_LOGICAL_NAMES = [
    "post-item",
    "post-author",
    "post-text",
    "post-likes",
    "comment-item",
    "comment-author",
    "comment-text",
    # docs/REQUIREMENTS.md section 9 entry 23 (Phase 2 بند 6): /spa-catalog's
    # own CSS-in-JS-shaped fields -- rotated the same way every other
    # element here is, no separate MarkupRandomizer instance needed.
    "spa-image",
    "spa-title",
    "spa-price",
    "spa-button",
]


def create_app(
    config: MockTargetConfig | None = None,
    ab_variant_rand_fn: Callable[[], float] | None = None,
) -> Flask:
    """Build the mock-target Flask app.

    Every challenge layer reads its own toggle from ``config`` at request
    time (not baked in at import time), so tests can build multiple app
    instances with different configs in the same process.

    ``ab_variant_rand_fn`` is injectable (see
    ``structural.ab_variant.choose_variant``) so a test can pin which A/B
    variant a request gets instead of depending on real randomness.
    """
    cfg = config or get_config()
    app = Flask(__name__)
    # Pure functions, safe to register once at app-build time rather than
    # per request -- see structural/shadow_dom.py.
    app.jinja_env.globals["is_shadow_wrapped"] = is_shadow_wrapped
    app.jinja_env.globals["encode_shadow_payload"] = encode_shadow_payload

    app.config["MOCK_TARGET_CONFIG"] = cfg
    app.config["MARKUP_RANDOMIZER"] = MarkupRandomizer(
        RANDOMIZED_LOGICAL_NAMES,
        interval_seconds=cfg.markup_randomizer_interval_minutes * 60,
    )
    app.config["FEED_RATE_LIMITER"] = FeedRateLimiter(
        threshold=cfg.feed_rate_limit_threshold,
        window_seconds=cfg.feed_rate_limit_window_seconds,
    )
    honeypot_logger = get_file_logger("mock_target.honeypot", cfg.honeypot_log_path)
    botd_logger = get_file_logger("mock_target.botd", cfg.botd_log_path)
    ja4_logger = get_file_logger("mock_target.ja4", cfg.ja4_log_path)
    fingerprint_logger = get_file_logger("mock_target.fingerprint", cfg.fingerprint_log_path)
    referer_session_logger = get_file_logger(
        "mock_target.referer_session", cfg.referer_session_log_path
    )
    app.config["CSRF_TOKEN_STORE"] = CsrfTokenStore()
    app.config["AUTH_SESSION_STORE"] = SessionStore(ttl_seconds=cfg.session_ttl_seconds)

    @app.before_request
    def _log_ja4_fingerprint() -> None:
        # Runs for every route, not just new ones -- see
        # security/ja4_integration.py's own docstring for why this is
        # structurally a no-op for every existing route (none of them
        # are ever reached through the JA4 proxy, so the header is
        # simply never present on those requests).
        log_ja4_fingerprint(ja4_logger, request.headers.get(JA4_HEADER_NAME))

    def _classes() -> dict[str, str]:
        randomizer: MarkupRandomizer = app.config["MARKUP_RANDOMIZER"]
        if not cfg.enable_markup_randomizer:
            return dict.fromkeys(RANDOMIZED_LOGICAL_NAMES, "")
        return {name: randomizer.get_class(name) for name in RANDOMIZED_LOGICAL_NAMES}

    def _session_seed() -> str:
        seed = request.cookies.get(SESSION_COOKIE_NAME)
        return seed if seed else secrets.token_hex(8)

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/")
    def index() -> Response:
        if cfg.enable_cookie_wall and not has_consent(request.cookies.get(CONSENT_COOKIE_NAME)):
            # Real content is genuinely absent here -- see
            # structural/cookie_wall.py's docstring for why this is a
            # server-side gate, not a CSS overlay.
            return Response(render_template("cookie_wall.html", accept_path=ACCEPT_PATH))

        seed = _session_seed()
        posts = generate_feed_page(seed, page=0, page_size=INDEX_PAGE_SIZE)

        decoy = None
        if cfg.enable_decoy_data and posts:
            decoy = generate_decoy_twin(posts[0], seed)

        honeypots = generate_honeypot_links() if cfg.enable_honeypots else []
        # Chosen fresh per request (not pinned to the session) -- see
        # structural/ab_variant.py's docstring for why.
        container_tag = (
            container_tag_for(choose_variant(rand_fn=ab_variant_rand_fn))
            if cfg.enable_ab_variants
            else "article"
        )

        response = Response(
            render_template(
                "index.html",
                posts=posts,
                decoy=decoy,
                honeypots=honeypots,
                classes=_classes(),
                botd_enabled=cfg.enable_botd,
                botd_script_path=VENDORED_SCRIPT_PATH,
                fingerprint_scoring_enabled=cfg.enable_fingerprint_scoring,
                container_tag=container_tag,
                placeholder_enabled=cfg.enable_placeholder_content,
                placeholder_text=PLACEHOLDER_TEXT,
                placeholder_swap_script=(
                    render_swap_script(cfg.placeholder_delay_ms)
                    if cfg.enable_placeholder_content
                    else None
                ),
                shadow_dom_enabled=cfg.enable_shadow_dom,
                shadow_attach_script=(SHADOW_ATTACH_SCRIPT if cfg.enable_shadow_dom else None),
            )
        )
        response.set_cookie(SESSION_COOKIE_NAME, seed)
        return response

    @app.get(ACCEPT_PATH)
    def accept_cookies() -> Response:
        response = redirect("/")
        response.set_cookie(CONSENT_COOKIE_NAME, CONSENT_COOKIE_VALUE)
        return response

    @app.get("/feed")
    def feed() -> Response:
        seed = _session_seed()
        response = Response(
            render_template(
                "feed.html",
                classes=_classes(),
                page_size=cfg.feed_page_size,
                dom_virtualization_enabled=cfg.enable_dom_virtualization,
                dom_virtualization_window_size=cfg.dom_virtualization_window_size,
            )
        )
        response.set_cookie(SESSION_COOKIE_NAME, seed)
        return response

    @app.get("/api/feed")
    def api_feed() -> Response | tuple[Response, int] | tuple[Response, int, dict[str, str]]:
        limiter: FeedRateLimiter = app.config["FEED_RATE_LIMITER"]
        client_key = request.remote_addr or "unknown"
        result = limiter.check(client_key)
        if not result.allowed:
            return (
                jsonify(
                    {"error": "rate_limited", "retry_after_seconds": result.retry_after_seconds}
                ),
                429,
                {"Retry-After": str(result.retry_after_seconds)},
            )

        seed = _session_seed()
        after_cursor = request.args.get("after")
        page_size = cfg.feed_page_size
        try:
            page = build_feed_page(seed, after_cursor, page_size)
        except ValueError as exc:
            return jsonify({"error": "invalid_cursor", "detail": str(exc)}), 400

        return jsonify(
            {
                "edges": [
                    {
                        "post": {
                            "id": post.post_id,
                            "author": post.author,
                            "text": post.text,
                            "likes": post.likes,
                        },
                        "comments": [_comment_to_dict(c) for c in post.comments],
                    }
                    for post in page.posts
                ],
                "page_info": {
                    "end_cursor": page.end_cursor,
                    "has_next_page": page.has_next_page,
                },
            }
        )

    @app.get("/spa-catalog")
    def spa_catalog() -> Response:
        # docs/REQUIREMENTS.md section 9 entry 23 (Phase 2 بند 6, Known
        # Limitation #5's real fix): no antibot challenge here at all --
        # this is purely a hydration-delay + CSS-in-JS-shaped extraction
        # target, unrelated to Anubis/Camoufox/Patchright/session
        # machinery. Session-seeded the same way /feed is (same
        # seed -> same catalog), purely so a repeat visit within one
        # session sees stable content -- not a security boundary.
        seed = _session_seed()
        products = generate_catalog(seed, cfg.spa_catalog_product_count)
        response = Response(
            render_template(
                "spa_catalog.html",
                products_json=products_to_payload(products),
                classes=_classes(),
                hydration_delay_ms=cfg.spa_hydration_delay_ms,
                skeleton_text=HYDRATION_SKELETON_TEXT,
            )
        )
        response.set_cookie(SESSION_COOKIE_NAME, seed)
        return response

    @app.get("/login")
    def login_page() -> Response:
        csrf_store: CsrfTokenStore = app.config["CSRF_TOKEN_STORE"]
        # Fresh every load, never fixed -- see security/auth.py's
        # CsrfTokenStore docstring.
        token = csrf_store.issue()
        return Response(render_template("login.html", csrf_token=token))

    @app.post("/login")
    def login_submit() -> Response | tuple[Response, int]:
        csrf_store: CsrfTokenStore = app.config["CSRF_TOKEN_STORE"]
        session_store: SessionStore = app.config["AUTH_SESSION_STORE"]
        token = request.form.get(CSRF_FIELD_NAME)
        if not csrf_store.consume(token):
            return jsonify({"error": "invalid_csrf_token"}), 403
        username = request.form.get(USERNAME_FIELD_NAME, "")
        password = request.form.get(PASSWORD_FIELD_NAME, "")
        if not check_credentials(username, password):
            return jsonify({"error": "invalid_credentials"}), 401
        session_token = session_store.issue(username)
        response = redirect("/feed-protected")
        response.set_cookie(AUTH_SESSION_COOKIE_NAME, session_token, httponly=True)
        return response

    @app.get("/feed-protected")
    def feed_protected() -> Response | tuple[Response, int]:
        session_store: SessionStore = app.config["AUTH_SESSION_STORE"]
        if not session_store.is_valid(request.cookies.get(AUTH_SESSION_COOKIE_NAME)):
            # A real, explicit 401 -- not a redirect to /login -- per the
            # user's own explicit choice: clearer for testing, and a
            # common shape real APIs actually have.
            return jsonify({"error": "unauthorized"}), 401

        try:
            page = int(request.args.get("page", "0"))
        except ValueError:
            return jsonify({"error": "invalid_page"}), 400
        if page < 0:
            return jsonify({"error": "invalid_page"}), 400

        seed = _session_seed()
        posts = generate_feed_page(seed, page=page, page_size=cfg.protected_feed_page_size)
        next_page = (
            f"/feed-protected?page={page + 1}"
            if page + 1 < cfg.protected_feed_total_pages
            else None
        )
        response = Response(
            render_template("feed_protected.html", posts=posts, next_page=next_page)
        )
        response.set_cookie(SESSION_COOKIE_NAME, seed)
        return response

    @app.get("/feed-interstitial")
    def feed_interstitial() -> Response:
        seed = _session_seed()
        response = Response(
            render_template(
                "feed_interstitial.html",
                interstitial_script=render_interstitial_script(
                    cfg.interstitial_trigger,
                    cfg.interstitial_delay_ms,
                    cfg.interstitial_scroll_percent,
                ),
            )
        )
        response.set_cookie(SESSION_COOKIE_NAME, seed)
        return response

    @app.get("/api/feed-interstitial")
    def api_feed_interstitial() -> Response | tuple[Response, int]:
        seed = _session_seed()
        after_cursor = request.args.get("after")
        try:
            page = build_interstitial_feed_page(
                seed,
                after_cursor,
                cfg.interstitial_feed_page_size,
                cfg.interstitial_feed_total_batches,
            )
        except ValueError as exc:
            return jsonify({"error": "invalid_cursor", "detail": str(exc)}), 400

        return jsonify(
            {
                "edges": [
                    {
                        "post": {
                            "id": post.post_id,
                            "author": post.author,
                            "text": post.text,
                            "likes": post.likes,
                        }
                    }
                    for post in page.posts
                ],
                "page_info": {
                    "end_cursor": page.end_cursor,
                    "has_next_page": page.has_next_page,
                },
            }
        )

    @app.get("/test-expire-session")
    def test_expire_session() -> Response:
        """Test-only instrumentation -- never part of any real login flow,
        same shape as ``/honeypot-trap/<token>``/``/botd-report``: a real
        route that exists purely to make otherwise-unobservable behavior
        (a live crawl reacting to a session expiring *mid-crawl*)
        deterministically testable, instead of depending on a real,
        flaky multi-second TTL wait. Deliberately expires the *caller's
        own* current session immediately -- see
        security/auth.py's SessionStore.force_expire docstring."""
        session_store: SessionStore = app.config["AUTH_SESSION_STORE"]
        token = request.cookies.get(AUTH_SESSION_COOKIE_NAME)
        expired = session_store.force_expire(token)
        return jsonify({"status": "expired" if expired else "no_session"})

    @app.get("/reject-pattern")
    def reject_pattern() -> Response | tuple[Response, int]:
        """Test-only instrumentation -- same shape as
        ``/test-expire-session``/``/honeypot-trap/<token>`` above: a real
        route that exists purely to make src/response_classifier.py's
        three named ``ResponsePattern`` values deterministically
        testable against real HTTP responses, not just hand-built dicts
        in a unit test (docs/REQUIREMENTS.md section 9 entry 29,
        "الطبقة 2" -- Protection Classifier). Always returns 403;
        ``?pattern=`` selects *how* it rejects:

        - ``empty`` (default): a completely empty body, no distinctive
          header -- ``ResponsePattern.SILENT_BLOCK``.
        - ``headers``: an empty body but carries
          ``src.response_classifier.KNOWN_BLOCK_HEADERS``' own
          deliberately-synthetic ``X-Antibot-Block`` header --
          ``ResponsePattern.HEADER_FINGERPRINTED``.
        - ``challenge``: a full HTML page containing
          ``src.response_classifier.KNOWN_CHALLENGE_MARKERS``' own
          ``titan-apex-mock-challenge`` marker (plus a generic "verify
          you are human" phrase, for realism) --
          ``ResponsePattern.CHALLENGE_PAGE``.

        Deliberately allowed straight through Anubis (test-environment/
        anubis/botPolicy.yaml's own ALLOW rule, same reasoning as
        ``/warmup-home``/``/spa-catalog`` above) -- this route's own 403
        is the thing under test, not Anubis's unrelated challenge page.
        """
        pattern = request.args.get("pattern", "empty")
        if pattern == "empty":
            return Response(status=403)
        if pattern == "headers":
            return Response(status=403, headers={"X-Antibot-Block": "titan-apex-mock"})
        if pattern == "challenge":
            return Response(
                "<html><head><title>Access Denied</title></head>"
                "<body><h1>titan-apex-mock-challenge</h1>"
                "<p>Please verify you are human to continue.</p></body></html>",
                status=403,
                mimetype="text/html",
            )
        return jsonify({"error": f"unknown pattern {pattern!r}"}), 400

    @app.post("/botd-report")
    def botd_report() -> Response:
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        log_botd_report(botd_logger, payload)
        return jsonify({"status": "logged"})

    @app.post("/fingerprint-report")
    def fingerprint_report() -> Response:
        # docs/REQUIREMENTS.md section 9 entry 19: same "log-only,
        # never enforce" shape as /botd-report above -- see
        # security/fpscanner_integration.py's own module docstring for
        # the two signals collected client-side and why this stays a
        # score, not a verdict.
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        log_fingerprint_report(fingerprint_logger, payload)
        return jsonify({"status": "logged"})

    @app.get("/honeypot-trap/<token>")
    def honeypot_trap(token: str) -> Response:
        log_honeypot_trigger(
            honeypot_logger,
            token=token,
            path=request.path,
            remote_addr=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        return jsonify({"status": "ok"})

    # docs/REQUIREMENTS.md section 9 entry 21, Step 1 (Referer path
    # consistency + session warm-up, Levels 1/2): a real, tiny navigation
    # chain -- /warmup-home -> /warmup-category -> /warmup-target --
    # deliberately allowed straight through Anubis (test-environment/
    # anubis/botPolicy.yaml's own new ALLOW rule) so a *plain* Scrapy
    # crawl (no antibot solving at all) can exercise the actual Referer/
    # cookie mechanics this entry is about, in isolation from the
    # separate, much bigger architectural question (documented, deferred
    # to Step 2) of how a real, browser-driven antibot solve would need
    # to carry a warm-up chain through its own single, continuous
    # session.
    @app.get("/warmup-home")
    def warmup_home() -> Response:
        has_cookie = request.cookies.get(WARMUP_SESSION_COOKIE_NAME) is not None
        if cfg.enable_referer_session_check:
            log_referer_session_check(
                referer_session_logger, request.path, request.referrer, has_cookie
            )
        response = Response(render_template("warmup_home.html"))
        # Only issued here, never refreshed on /warmup-category or
        # /warmup-target -- if either of those silently reissued it when
        # missing, a cold, disconnected hit would always look identical
        # to a real warmed-up one, defeating the whole point of Level 2's
        # cookie check.
        if not has_cookie:
            response.set_cookie(WARMUP_SESSION_COOKIE_NAME, secrets.token_hex(8))
        return response

    @app.get("/warmup-category")
    def warmup_category() -> Response:
        has_cookie = request.cookies.get(WARMUP_SESSION_COOKIE_NAME) is not None
        if cfg.enable_referer_session_check:
            log_referer_session_check(
                referer_session_logger, request.path, request.referrer, has_cookie
            )
        return Response(render_template("warmup_category.html"))

    @app.get("/warmup-target")
    def warmup_target() -> Response:
        has_cookie = request.cookies.get(WARMUP_SESSION_COOKIE_NAME) is not None
        if cfg.enable_referer_session_check:
            log_referer_session_check(
                referer_session_logger, request.path, request.referrer, has_cookie
            )
        return Response(render_template("warmup_target.html"))

    return app


def _comment_to_dict(comment: Any) -> dict[str, Any]:
    return {
        "id": comment.comment_id,
        "author": comment.author,
        "text": comment.text,
        "replies": [_comment_to_dict(r) for r in comment.replies],
    }


if __name__ == "__main__":  # pragma: no cover -- exercised by running the container, not pytest
    application = create_app()
    application.run(host="0.0.0.0", port=8000)  # noqa: S104 -- isolated Docker network only, see README
