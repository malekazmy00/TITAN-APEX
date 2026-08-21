"""Loading-placeholder leakage: every real post's text is initially
rendered as a literal placeholder string server-side, swapped in for the
real text by client-side JS only after a short delay.

A scraper that reads the raw, un-rendered HTML (a plain HTTP fetch, or a
real-browser render that doesn't wait long enough past `load`) captures
this placeholder text as if it were real data -- a genuine, well-formed
item with the wrong content, the same "looks fine, is actually wrong"
data-quality class structural/decoy_data.py already documents for the
hidden decoy twin. Unlike the decoy (a whole extra, hidden post), this is
about *one field of a real, visible post* being temporarily wrong.
"""

from __future__ import annotations

PLACEHOLDER_TEXT = "Loading..."
DEFAULT_DELAY_MS = 500


def render_swap_script(delay_ms: int) -> str:
    """Inline JS that swaps every ``[data-real-text]`` element's visible
    text for its real content, after ``delay_ms`` milliseconds.

    Raises:
        ValueError: if ``delay_ms`` is not positive.
    """
    if delay_ms <= 0:
        raise ValueError(f"delay_ms must be > 0, got {delay_ms}")

    return (
        "setTimeout(() => {"
        "document.querySelectorAll('[data-real-text]').forEach((el) => {"
        "el.textContent = el.dataset.realText;"
        "el.removeAttribute('data-real-text');"
        "});"
        f"}}, {delay_ms});"
    )
