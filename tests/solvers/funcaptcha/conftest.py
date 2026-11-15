"""Make the funcaptcha test helpers importable beside the test modules.

The suite has no __init__.py files, so each test package carries its own path
hook; this mirrors tests/tools/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
