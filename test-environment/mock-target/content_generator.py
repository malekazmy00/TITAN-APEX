"""Fake data generation for the mock-target social-feed challenge.

100% synthetic (Faker) -- docs/REQUIREMENTS.md section 4's isolation rule:
zero real data from any source. Every generator is seeded so output is
reproducible for a given seed (needed for the per-session content variance
in structural/feed.py: same session -> same feed; different session ->
different order/content, like a real recommendation feed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faker import Faker


@dataclass
class Comment:
    comment_id: str
    author: str
    text: str
    replies: list[Comment] = field(default_factory=list)


@dataclass
class Post:
    post_id: str
    author: str
    text: str
    likes: int
    comments: list[Comment] = field(default_factory=list)


@dataclass
class Product:
    """One /spa-catalog item (docs/REQUIREMENTS.md section 9 entry 23,
    Known Limitation #5's real fix -- a CSS-in-JS-shaped SPA catalog)."""

    product_id: str
    title: str
    price: float
    image_url: str


def faker_for_seed(seed: str) -> Faker:
    fake = Faker()
    Faker.seed(seed)
    fake.seed_instance(seed)
    return fake


def generate_post(seed: str, index: int) -> Post:
    """Build one deterministic fake post for (seed, index).

    Raises:
        ValueError: if ``index`` is negative -- there is no such post.
    """
    if index < 0:
        raise ValueError(f"post index must be >= 0, got {index}")

    fake = faker_for_seed(f"{seed}:post:{index}")
    return Post(
        post_id=f"{seed}-post-{index}",
        author=fake.user_name(),
        text=fake.paragraph(nb_sentences=3),
        likes=fake.random_int(min=0, max=5000),
    )


def generate_comment(seed: str, post_index: int, comment_index: int, depth: int = 0) -> Comment:
    """Build one deterministic fake comment, optionally with nested replies.

    Raises:
        ValueError: if ``depth`` is negative -- nesting can't go backwards.
    """
    if depth < 0:
        raise ValueError(f"comment depth must be >= 0, got {depth}")

    fake = faker_for_seed(f"{seed}:post:{post_index}:comment:{comment_index}:depth:{depth}")
    replies: list[Comment] = []
    if depth == 0 and fake.boolean(chance_of_getting_true=40):
        reply_count = fake.random_int(min=1, max=2)
        replies = [
            generate_comment(seed, post_index, comment_index * 10 + i, depth=depth + 1)
            for i in range(reply_count)
        ]

    return Comment(
        comment_id=f"{seed}-post-{post_index}-comment-{comment_index}-{depth}",
        author=fake.user_name(),
        text=fake.sentence(nb_words=10),
        replies=replies,
    )


def generate_feed_page(
    seed: str, page: int, page_size: int, comments_per_post: int = 2
) -> list[Post]:
    """Build one deterministic page of posts (each with nested comments).

    ``seed`` is normally a per-session token: the same seed always yields
    the same feed, but two different sessions get different post order and
    content -- the same shape a real feed-ranking algorithm has, and a
    real test of whether a scraper wrongly assumes a fixed global order.

    Raises:
        ValueError: if ``page`` or ``page_size`` is not positive.
    """
    if page < 0:
        raise ValueError(f"page must be >= 0, got {page}")
    if page_size <= 0:
        raise ValueError(f"page_size must be > 0, got {page_size}")

    start = page * page_size
    posts = []
    for i in range(start, start + page_size):
        post = generate_post(seed, i)
        post.comments = [
            generate_comment(seed, i, c) for c in range(comments_per_post)
        ]
        posts.append(post)
    return posts


def generate_product(seed: str, index: int) -> Product:
    """Build one deterministic fake catalog product for (seed, index).

    Raises:
        ValueError: if ``index`` is negative -- there is no such product.
    """
    if index < 0:
        raise ValueError(f"product index must be >= 0, got {index}")

    fake = faker_for_seed(f"{seed}:product:{index}")
    return Product(
        product_id=f"{seed}-product-{index}",
        title=fake.catch_phrase(),
        price=round(fake.pyfloat(min_value=1, max_value=500, right_digits=2), 2),
        image_url=f"/spa-catalog/img/{fake.md5()[:12]}.png",
    )


def generate_catalog(seed: str, count: int) -> list[Product]:
    """Build ``count`` deterministic fake catalog products for ``seed``
    -- same session (or explicit seed) -> same catalog, a different one
    -> different products, the same shape ``generate_feed_page`` already
    has for posts.

    Raises:
        ValueError: if ``count`` is not positive.
    """
    if count <= 0:
        raise ValueError(f"count must be > 0, got {count}")
    return [generate_product(seed, i) for i in range(count)]
