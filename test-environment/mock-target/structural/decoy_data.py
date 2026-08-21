"""A stale, hidden twin of a real post -- rendered with the identical CSS
class as the real (visible) one, but *before* it in DOM order.

GenericSpider's parse() takes ``row.css(field_selector).getall()`` and
picks the *first* match when there's exactly one, or a list otherwise --
it has no concept of "which of these is actually visible". If a scraper's
selector matches both the decoy and the real element and happens to grab
the decoy's (older, different) text, that's a genuine data-quality bug,
not a network/environment issue -- see docs/REQUIREMENTS.md section 8.
"""

from __future__ import annotations

from dataclasses import replace

from content_generator import Post, faker_for_seed


def generate_decoy_twin(real_post: Post, seed: str) -> Post:
    """Build a stale twin of ``real_post``: same id, different text/likes.

    Raises:
        ValueError: if ``real_post.post_id`` is empty -- there's nothing to
            twin.
    """
    if not real_post.post_id:
        raise ValueError("real_post.post_id must be non-empty")

    fake = faker_for_seed(f"{seed}:decoy:{real_post.post_id}")
    return replace(
        real_post,
        text=fake.paragraph(nb_sentences=2),
        likes=fake.random_int(min=0, max=real_post.likes if real_post.likes > 0 else 1),
        comments=[],
    )
