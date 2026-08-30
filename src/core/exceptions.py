"""Custom exception hierarchy for TITAN-APEX.

Every module in this project must raise (and callers must catch) one of
these specific exceptions instead of the built-in ``Exception``. This is a
hard project rule (see docs/REQUIREMENTS.md, section 3): a bare
``except Exception`` or ``except: pass`` is not allowed anywhere in the
codebase.
"""

from __future__ import annotations


class TitanApexError(Exception):
    """Base class for every exception raised by this project."""


class ConfigError(TitanApexError):
    """Raised when a YAML/env configuration file is missing or invalid."""


class SpiderError(TitanApexError):
    """Raised for spider-level failures (parsing, selector, target)."""


class StorageError(TitanApexError):
    """Raised by a :class:`StorageBackend` implementation on failure."""


class AntibotError(TitanApexError):
    """Raised by an :class:`AntibotProvider` implementation on failure."""


class BrowserCrashedError(AntibotError):
    """Raised when the underlying browser engine itself dies mid-solve --
    a genuine ``page.on("crash")``/``browser.on("disconnected")`` event,
    not any other kind of solve failure (a denied request, no items found,
    a legitimate timeout). docs/REQUIREMENTS.md section 9 entry 17: a
    real, kernel-log-confirmed Firefox/``libxul.so`` engine segfault (a
    long-standing, unresolved-upstream class of Firefox crash, not
    something this project's own code -- or a shared-memory/OOM
    constraint -- causes or can fix), so the accepted mitigation is
    detecting it explicitly and retrying the whole solve on a fresh
    browser instance, rather than trying to prevent the crash itself.
    Still an :class:`AntibotError` (every existing ``except AntibotError``
    catch site keeps working unchanged) -- this subclass exists
    specifically so a caller that *does* want to distinguish "the browser
    engine itself died" from every other failure reason can."""


class AIAnalyzerError(TitanApexError):
    """Raised by an :class:`AIAnalyzer` implementation on failure."""


class RenderError(TitanApexError):
    """Raised when a headless-browser render (e.g. Playwright) fails."""


class QueueError(TitanApexError):
    """Raised by the task queue (Redis/RQ) on connection or job failure."""
