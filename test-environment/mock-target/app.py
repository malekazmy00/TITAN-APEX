"""Mock scraping-training target: Flask app tying every challenge layer
together. See test-environment/README.md for what each route/layer is
for and how to verify it's actually active.
"""

from __future__ import annotations

import secrets
from typing import Any

from config import MockTargetConfig, get_config
from content_generator import generate_feed_page
from flask import Flask, Response, jsonify, redirect, render_template, request
from security.botd_integration import VENDORED_SCRIPT_PATH, log_botd_report
from security.file_logger import get_file_logger
from security.honeypot_logger import log_honeypot_trigger
from structural.cookie_wall import (
    ACCEPT_PATH,
    CONSENT_COOKIE_NAME,
    CONSENT_COOKIE_VALUE,
    has_consent,
)
from structural.decoy_data import generate_decoy_twin
from structural.feed import FeedRateLimiter, build_feed_page
from structural.honeypots import generate_honeypot_links
from structural.markup_randomizer import MarkupRandomizer

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
]


def create_app(config: MockTargetConfig | None = None) -> Flask:
    """Build the mock-target Flask app.

    Every challenge layer reads its own toggle from ``config`` at request
    time (not baked in at import time), so tests can build multiple app
    instances with different configs in the same process.
    """
    cfg = config or get_config()
    app = Flask(__name__)

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

        response = Response(
            render_template(
                "index.html",
                posts=posts,
                decoy=decoy,
                honeypots=honeypots,
                classes=_classes(),
                botd_enabled=cfg.enable_botd,
                botd_script_path=VENDORED_SCRIPT_PATH,
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

    @app.post("/botd-report")
    def botd_report() -> Response:
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        log_botd_report(botd_logger, payload)
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
