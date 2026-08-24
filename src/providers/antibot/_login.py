"""Login-form-submission helper for
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider` and
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`'s
browser-driving solve functions -- docs/REQUIREMENTS.md section 9 entry
15 (Known Limitation #1: login/session, activated ahead of Interstitials
per explicit user request).

Fills the real DOM login form and clicks submit -- the browser submits
the form's own hidden CSRF token field automatically, exactly as a real
user's browser would; this module never reads or reconstructs the token
itself. The caller (``_default_camoufox_solve``/``_default_patchright_solve``)
is responsible for tracking whatever navigation the submit triggers (both
already track the last real main-frame navigation response for their own,
pre-existing reasons -- see each provider module's own docstring) and
deciding what it means: a redirect to the real target is success, staying
on the login page with a non-2xx status is a real, evidenced failure.

Typed loosely (``Any`` for the live Playwright/Patchright ``Page``
object), same tradeoff as ``src.providers.antibot._scroll``/``_live_dom``'s
own module docstrings.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def submit_login_form(
    page: Any,
    login_url: str,
    username: str,
    password: str,
    username_field: str,
    password_field: str,
    submit_selector: str,
    timeout_ms: int,
) -> None:
    """Navigates to ``login_url``, fills ``username_field``/``password_field``
    with ``username``/``password``, and clicks ``submit_selector``.

    Raises:
        ValueError: if ``login_url``, ``username``, ``password``,
            ``username_field``, ``password_field``, or ``submit_selector``
            is empty -- a login attempt missing any of these means
            nothing.
    """
    if not login_url:
        raise ValueError("login_url must be non-empty")
    if not username:
        raise ValueError("username must be non-empty")
    if not password:
        raise ValueError("password must be non-empty")
    if not username_field:
        raise ValueError("username_field must be non-empty")
    if not password_field:
        raise ValueError("password_field must be non-empty")
    if not submit_selector:
        raise ValueError("submit_selector must be non-empty")

    page.goto(login_url, timeout=timeout_ms)
    page.fill(username_field, username)
    page.fill(password_field, password)
    page.click(submit_selector, timeout=timeout_ms)


def perform_login_and_navigate(
    page: Any,
    login_url: str,
    username: str,
    password: str,
    username_field: str,
    password_field: str,
    submit_selector: str,
    timeout_ms: int,
    post_load_wait_ms: int,
    get_last_status: Callable[[], int | None],
    target_url: str,
    session_expiry_probe_url: str | None,
) -> bool:
    """Runs the full login sequence via :func:`submit_login_form`, waits
    for the result to settle, then decides what happens next based on
    ``get_last_status()`` -- a callable the caller provides, closing over
    its own last-main-frame-navigation-response tracking (both
    ``_default_camoufox_solve`` and ``_default_patchright_solve`` already
    maintain one for other reasons; this reuses it rather than
    duplicating that tracking here).

    On success (``get_last_status()`` is ``None`` or ``< 400`` --
    matching the same "no response object at all isn't itself a
    failure" contract the rest of this module has), optionally visits
    ``session_expiry_probe_url`` (see
    :class:`~src.core.interfaces.antibot_provider.LoginFlow`'s own
    docstring for why this exists at all) and then navigates to
    ``target_url`` -- both via ``page.goto()``, so the caller's own
    tracking naturally reflects whichever is genuinely the final state
    by the time this returns. On failure, does *not* navigate anywhere
    else -- the login page's own failure response is left as the final
    state for the caller to read via its own tracking.

    Returns ``True`` if the login POST itself succeeded, ``False`` if it
    didn't -- the caller decides what to log (this function does no
    logging itself, so provider-specific log-event names stay in each
    provider's own module).
    """
    submit_login_form(
        page, login_url, username, password, username_field, password_field, submit_selector,
        timeout_ms,
    )
    page.wait_for_timeout(post_load_wait_ms)
    status = get_last_status()
    if status is not None and status >= 400:
        return False
    if session_expiry_probe_url:
        page.goto(session_expiry_probe_url, timeout=timeout_ms)
    page.goto(target_url, timeout=timeout_ms)
    return True
