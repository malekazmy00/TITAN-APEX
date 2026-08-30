"""Unit tests for src/providers/antibot/_mouse_movement.py.

A fake ``page.mouse`` tracks every ``.move()`` call -- no real browser,
no real oxymouse randomness involved: :func:`move_mouse_along_path`'s own
consumption logic is tested against an injected, fixed-output
``PathGenerator`` (this module's own docstring explains why the path
generator itself, not a seed, is the injection point -- oxymouse's own
algorithms draw from Python's global ``random`` module directly and
can't be seeded).
"""

from __future__ import annotations

import pytest

from src.providers.antibot._mouse_movement import (
    DEFAULT_MOUSE_PATH_ALGORITHM,
    move_mouse_along_path,
    oxymouse_path_generator,
)


class _FakeMouse:
    def __init__(self) -> None:
        self.move_calls: list[tuple[int, int]] = []

    def move(self, x: int, y: int) -> None:
        self.move_calls.append((x, y))


class _FakePage:
    def __init__(self) -> None:
        self.mouse = _FakeMouse()


def test_walks_every_point_the_generator_returns_in_order() -> None:
    """Happy path: every intermediate point from the injected generator
    is passed to page.mouse.move(), in the order the generator returned
    them."""
    page = _FakePage()
    recorded_args: list[tuple[int, int, int, int]] = []

    def fake_generator(from_x: int, from_y: int, to_x: int, to_y: int) -> list[tuple[int, int]]:
        recorded_args.append((from_x, from_y, to_x, to_y))
        return [(210, 210), (400, 300), (600, 400)]

    move_mouse_along_path(page, 200, 200, 600, 400, fake_generator)

    assert recorded_args == [(200, 200, 600, 400)]
    # The generator's own points, then the always-appended final exact
    # move (this module's own docstring: papers over gaussian/perlin's
    # confirmed unreliability, harmless when the generator already
    # finished on target, as bezier's own real output does).
    assert page.mouse.move_calls == [(210, 210), (400, 300), (600, 400), (600, 400)]


def test_always_appends_final_exact_move_even_when_generator_overshoots() -> None:
    """The concrete real defect this module's own docstring documents by
    hand for oxymouse's "gaussian" algorithm: a path whose own last point
    is nowhere near the true destination still ends on it for real,
    because the final move is unconditional, not "only if needed"."""
    page = _FakePage()

    def overshooting_generator(
        from_x: int, from_y: int, to_x: int, to_y: int
    ) -> list[tuple[int, int]]:
        return [(914, 467), (931, 467)]  # nowhere near (600, 400)

    move_mouse_along_path(page, 200, 200, 600, 400, overshooting_generator)

    assert page.mouse.move_calls[-1] == (600, 400)


def test_raises_value_error_on_empty_path() -> None:
    """A generator that produced no path at all isn't a real path --
    silently doing nothing but the final jump would hide that from a
    caller relying on genuinely gradual movement (this module's own
    docstring)."""
    page = _FakePage()

    def empty_generator(from_x: int, from_y: int, to_x: int, to_y: int) -> list[tuple[int, int]]:
        return []

    with pytest.raises(ValueError, match="empty path"):
        move_mouse_along_path(page, 200, 200, 600, 400, empty_generator)

    assert page.mouse.move_calls == []


def test_oxymouse_path_generator_default_algorithm_is_bezier() -> None:
    """docs/REQUIREMENTS.md section 9 entry 20: bezier is the only one of
    oxymouse's three algorithms confirmed by hand to reliably land on the
    requested endpoint (gaussian overshoots-then-teleports, perlin never
    reliably arrives at all) -- this is this module's own default for
    exactly that reason."""
    assert DEFAULT_MOUSE_PATH_ALGORITHM == "bezier"


def test_oxymouse_path_generator_builds_a_real_usable_generator() -> None:
    """A light, real (not faked) smoke check that the real oxymouse
    library is actually wired up correctly -- deliberately not asserting
    on the *shape* of the curve itself (that's oxymouse's own,
    non-seedable randomness, already characterized by hand in this
    module's own docstring, not something a unit test should pin down)."""
    generator = oxymouse_path_generator()
    path = generator(200, 200, 600, 400)

    assert isinstance(path, list)
    assert len(path) > 0
    assert all(isinstance(point, tuple) and len(point) == 2 for point in path)


def test_oxymouse_path_generator_rejects_an_invalid_algorithm() -> None:
    """Propagated straight from OxyMouse.__init__'s own identical check
    (this module's own docstring) -- surfaced at generator-build time,
    not silently swallowed."""
    with pytest.raises(ValueError):
        oxymouse_path_generator("not-a-real-algorithm")
