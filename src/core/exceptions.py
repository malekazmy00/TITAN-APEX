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


class AIAnalyzerError(TitanApexError):
    """Raised by an :class:`AIAnalyzer` implementation on failure."""


class RenderError(TitanApexError):
    """Raised when a headless-browser render (e.g. Playwright) fails."""
