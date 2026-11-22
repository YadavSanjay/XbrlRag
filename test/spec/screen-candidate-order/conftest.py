"""screen-candidate-order spec tests: the fixture readers live in ``sco_support.py``.

The gate runs in importlib mode, so this directory is put on the path for that module.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
