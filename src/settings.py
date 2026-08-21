"""Central, environment-driven settings.

Every configurable value lives here and is read from the environment (see
``.env.example`` for the full list). Nothing outside this module should
hardcode a path, host, or credential — see docs/REQUIREMENTS.md, section 4.
"""

from __future__ import annotations

import os


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    return float(value)


class Settings:
    """Snapshot of environment-driven configuration.

    Instantiated on demand (not at import time) so tests can freely set
    environment variables before constructing it.
    """

    def __init__(self) -> None:
        self.log_level: str = _env_str("TITAN_LOG_LEVEL", "INFO")
        self.storage_path: str = _env_str("TITAN_STORAGE_PATH", "data/titan_apex.sqlite3")
        self.default_download_delay: float = _env_float("TITAN_DOWNLOAD_DELAY", 1.0)
        self.retry_max_attempts: int = _env_int("TITAN_RETRY_MAX_ATTEMPTS", 5)
        self.retry_base_delay: float = _env_float("TITAN_RETRY_BASE_DELAY", 1.0)


def get_settings() -> Settings:
    """Build a fresh :class:`Settings` snapshot from the current environment."""
    return Settings()
