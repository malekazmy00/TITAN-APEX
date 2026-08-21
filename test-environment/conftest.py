"""Makes ``test-environment/mock-target`` importable for pytest.

``mock-target`` has a hyphen and can't be a dotted Python package name,
so it's never imported as one -- this just puts it on sys.path, the same
way an application's own directory is on sys.path when you run it
directly with ``python app.py``. Everything inside (``content_generator``,
``structural.feed``, ``security.file_logger``, ...) is then importable as
top-level/subpackage modules from that path, exactly as app.py itself
imports them.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MOCK_TARGET_DIR = Path(__file__).resolve().parent / "mock-target"
if str(_MOCK_TARGET_DIR) not in sys.path:
    sys.path.insert(0, str(_MOCK_TARGET_DIR))
