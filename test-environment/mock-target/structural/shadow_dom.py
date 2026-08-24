"""Real Shadow DOM encapsulation: every other post's content lives only
inside a genuine, client-side-attached shadow root, not a `display:none`
trick and not something already present in the raw HTML string a scraper
(or even a real browser's `page.content()`) ever reads.

Per the DOM spec: a shadow root attached imperatively via
``Element.attachShadow()`` is never included when serializing its host's
``outerHTML``/``innerHTML`` -- deliberately, that is what "encapsulation"
means. Playwright's ``page.content()`` (what every
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider`/
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`
call returns) is exactly ``document.documentElement.outerHTML`` under the
hood, so it inherits that same blind spot.

``GenericSpider.parse()`` (and every one of this project's three
``AntibotProvider``s) reads whichever browser/HTTP response body it gets
as a plain HTML *string* and runs Scrapy/parsel CSS selectors over that
string -- there is no live DOM to pierce, so a post rendered only inside
a shadow root is structurally invisible to it, even when Camoufox/
Patchright already drove a real, full browser past every other layer in
this stack (Anubis, the cookie wall, A/B variants, honeypots,
docs/REQUIREMENTS.md section 9 entry 10). This is a genuine,
different-in-kind gap from every prior structural challenge here:
honeypots/decoy-data are a *visibility* problem (the content is in the
same DOM string, just hidden); this is an *encapsulation* problem (the
content is never in that string at all).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from content_generator import Comment, Post


def is_shadow_wrapped(index: int) -> bool:
    """Whether the post at this 0-based position renders inside a real
    shadow root instead of plain light-DOM markup.

    Deterministic (every odd index), not random -- same reasoning
    structural/ab_variant.py's own module docstring gives for choosing
    its variant fresh per request instead of by a coin flip recorded
    nowhere: a real test needs a real, checkable expectation for how many
    posts this challenge removes from a given crawl, not "sometimes some
    number".

    Raises:
        ValueError: if ``index`` is negative -- there's no such post.
    """
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index}")
    return index % 2 == 1


def _comment_to_dict(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.comment_id,
        "author": comment.author,
        "text": comment.text,
        "replies": [_comment_to_dict(reply) for reply in comment.replies],
    }


def encode_shadow_payload(post: Post) -> str:
    """Base64(JSON)-encode ``post``'s full content for
    :data:`SHADOW_ATTACH_SCRIPT` (the client-side renderer) to build into
    a real shadow root.

    Base64, not raw JSON in an HTML attribute: Faker-generated
    ``author``/``text`` aren't guaranteed free of ``"``/``<``, which would
    otherwise risk breaking the surrounding attribute -- this way the
    attribute value is always a plain opaque token, no HTML-escaping
    question at all.

    Raises:
        ValueError: if ``post.post_id`` is empty -- there's nothing to encode.
    """
    if not post.post_id:
        raise ValueError("post.post_id must be non-empty")

    payload = {
        "id": post.post_id,
        "author": post.author,
        "text": post.text,
        "likes": post.likes,
        "comments": [_comment_to_dict(c) for c in post.comments],
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


# Attaches a real, `mode: "open"` shadow root (open, not closed -- this
# challenge is about a plain-string HTML parser's structural blind spot,
# not about hiding from a real browser's own devtools/Playwright
# locators too) to every `<mock-shadow-post>` placeholder, and builds its
# content from `data-shadow-payload` via safe DOM construction
# (`textContent`, never `innerHTML` string concatenation) -- Faker output
# is untrusted-shaped input even in this mock app, so this sidesteps any
# XSS-shaped question rather than relying on it being harmless.
SHADOW_ATTACH_SCRIPT = """
(() => {
  function buildComment(c) {
    const el = document.createElement('div');
    el.dataset.role = 'comment';
    el.dataset.commentId = c.id;
    const author = document.createElement('span');
    author.dataset.role = 'comment-author';
    author.textContent = c.author;
    const text = document.createElement('span');
    text.dataset.role = 'comment-text';
    text.textContent = c.text;
    el.append(author, text);
    for (const reply of c.replies) {
      el.append(buildComment(reply));
    }
    return el;
  }

  document.querySelectorAll('mock-shadow-post').forEach((host) => {
    const payload = JSON.parse(atob(host.dataset.shadowPayload));
    const shadow = host.attachShadow({ mode: 'open' });
    const article = document.createElement('article');
    article.dataset.role = 'post';
    article.dataset.postId = payload.id;
    const author = document.createElement('span');
    author.dataset.role = 'post-author';
    author.textContent = payload.author;
    const text = document.createElement('p');
    text.dataset.role = 'post-text';
    text.textContent = payload.text;
    const likes = document.createElement('span');
    likes.dataset.role = 'post-likes';
    likes.textContent = String(payload.likes);
    const comments = document.createElement('div');
    comments.dataset.role = 'comments';
    for (const c of payload.comments) {
      comments.append(buildComment(c));
    }
    article.append(author, text, likes, comments);
    shadow.append(article);
  });
})();
"""
