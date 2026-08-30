"""Human-like mouse-movement path generation -- docs/REQUIREMENTS.md
section 9 entry 20 (item 10's mouse-simulation phase, the last
remaining piece after JA4/TLS was confirmed a dead end for Camoufox
specifically, entry 19, and fpscanner's own log-only scoring, also
entry 19).

**Deliberately reuses entry 17's own ``hover_fn`` hook
(:func:`~src.providers.antibot._scroll.scroll_and_collect`) rather than
inventing a new architecture** -- ``hover_fn`` is already called
immediately before every scroll attempt, already the established
"reposition the cursor realistically before scrolling" point in the
pipeline (docs/REQUIREMENTS.md section 9 entry 17's "Seventh"/"Eighth"
revisions, and the click→hover→wheel ordering entry 9's own
cookie-consent-wall round first established). This module only
upgrades *how* that repositioning happens -- a real, curved, multi-step
path instead of a single instant jump -- not a new hook, not a new call
site.

**Why this was needed at all:** ``Locator.hover()`` (what
``camoufox_provider.py``/``patchright_provider.py``'s own
``_hover_feed_container_before_scroll`` calls today) snaps the cursor
directly to the target element's center in one jump -- confirmed by
reading Playwright's own source, not assumed: ``hover()`` calls
``mouse.move()`` with no intermediate steps. A real user's cursor
travels a continuous, curved path to get there.

**Library choice (oxymouse, not DaiCapra/Natural-Mouse-Movements-
Neural-Networks):** both were evaluated, per the research this phase is
based on. DaiCapra's project needs a full Keras/TensorFlow runtime plus
managing external pretrained model weight files -- a genuinely heavier
dependency footprint than this project's own established
"lightweight, pure-function-first" style justifies for what both that
same research and this project's own decision call an explicitly
*temporary* baseline (the real, long-term plan, docs/REQUIREMENTS.md
section 9 entry 20, is a recorded-corpus-based retrieval system
instead of either library -- see this module's own module docstring
note on ``docs/REQUIREMENTS.md``'s session-replay-bot detection
warning for why a corpus can never be replayed verbatim either).
oxymouse (PyPI, MIT) needed no model files at all and was immediately
usable.

**oxymouse's own three algorithms were tested directly, not assumed
equally good** (docs/REQUIREMENTS.md section 9 entry 20 has the exact
recorded output): only ``"bezier"`` reliably lands on the requested
endpoint with a smooth, monotonic curve. ``"gaussian"`` was observed to
overshoot far past the target and then teleport back to it as its
literal last point (a real defect, not a stealth feature).
``"perlin"``'s own generated path does not reliably reach the
destination at all -- confirmed by hand, its last point can land
nowhere near the requested endpoint. ``"bezier"`` is this module's
default for exactly this reason; :func:`move_mouse_along_path` also
always appends one final, exact move to the true destination
regardless of algorithm, specifically to paper over ``"gaussian"``/
``"perlin"``'s own confirmed unreliability rather than trust any
algorithm's own path to truly finish on target.

**Injectable path generator, not a seeded RNG** -- oxymouse's own
algorithms draw from Python's global ``random`` module directly
(confirmed by reading its source: ``bezier_mouse.py`` calls
``random.randint``/``random.uniform`` at module scope, never an
injected ``random.Random``), unlike every other randomized decision in
this project (``_scroll.py``'s own ``randomized_scroll_delta``/
``randomized_pause_ms``, both built around an injected
``random.Random``). Reseeding oxymouse's global state to make it
deterministic would be fragile and would leak into unrelated code.
Instead, the *path generator itself* is the injected dependency here --
the same shape entry 17's own ``trigger_and_wait_fn``/``hover_fn``
already established: a real, oxymouse-backed generator for every real
caller, a fake, fixed-output one for unit tests, so the *consumption*
logic (walking the path, calling ``page.mouse.move()`` for each point)
stays fully deterministic and testable without needing oxymouse itself
to be seedable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: ``(from_x, from_y, to_x, to_y) -> [(x, y), ...]`` -- a real path
#: generator never needs anything else injected (no RNG parameter: see
#: this module's own docstring for why the generator itself, not a
#: seed, is the injection point).
PathGenerator = Callable[[int, int, int, int], list[tuple[int, int]]]

DEFAULT_MOUSE_PATH_ALGORITHM = "bezier"


def oxymouse_path_generator(algorithm: str = DEFAULT_MOUSE_PATH_ALGORITHM) -> PathGenerator:
    """Builds a real, oxymouse-backed :data:`PathGenerator` for the given
    algorithm (``"bezier"``, ``"gaussian"``, or ``"perlin"`` -- oxymouse's
    own three; see this module's own docstring for why ``"bezier"`` is
    the recommended default). A deferred import -- oxymouse pulls in
    ``scipy``/``noise`` (a compiled Perlin-noise extension), genuinely
    unrelated to anything else this project does at any other call
    site, the same "only pay for it where it's actually used" reasoning
    ``camoufox_provider.py``'s own module docstring already documents
    for Camoufox/Playwright.

    Raises:
        ValueError: if ``algorithm`` isn't one oxymouse itself supports
            (propagated from ``OxyMouse.__init__``'s own identical
            check, just surfaced at generator-build time instead of
            first use).
    """
    from oxymouse import OxyMouse

    mouse = OxyMouse(algorithm)

    def _generate(from_x: int, from_y: int, to_x: int, to_y: int) -> list[tuple[int, int]]:
        return [(int(x), int(y)) for x, y in mouse.generate_coordinates(from_x, from_y, to_x, to_y)]

    return _generate


def move_mouse_along_path(
    page: Any,
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    path_generator: PathGenerator,
) -> None:
    """Moves ``page.mouse`` from ``(from_x, from_y)`` to ``(to_x, to_y)``
    through every intermediate point ``path_generator`` returns, instead
    of ``Locator.hover()``'s own single instant jump -- the actual real-
    input-shape upgrade this module exists for (this module's own
    docstring has the full reasoning).

    The final move is always to the *exact* requested ``(to_x, to_y)``,
    regardless of whatever point the generator's own path actually ends
    on -- confirmed necessary by hand (this module's own docstring):
    oxymouse's ``"gaussian"``/``"perlin"`` algorithms do not reliably
    finish on the requested endpoint at all. A caller wanting a bare,
    already-on-target path (e.g. ``"bezier"``'s own real output) simply
    gets one harmless, zero-distance extra move at the end.

    Raises:
        ValueError: if ``path_generator`` returns an empty list -- a
            generator that produced no path at all isn't a real path,
            and silently doing nothing but the final jump would hide
            that from a caller relying on genuinely gradual movement.
    """
    path = path_generator(from_x, from_y, to_x, to_y)
    if not path:
        raise ValueError("path_generator returned an empty path")
    for x, y in path:
        page.mouse.move(x, y)
    page.mouse.move(to_x, to_y)
