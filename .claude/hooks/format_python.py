"""PostToolUse auto-fix (WS-8 / H2): `ruff check --fix` on an edited Python file.

Runs after a successful Write/Edit/MultiEdit. No-op for non-`.py` files. Applies ruff's safe
fixes (import order, modern syntax) with the repo's `ruff.toml`, using the project (py312)
interpreter so the ruff version matches the pin in `requirements-dev.txt`. There is no formatter
(black/isort were retired). Always exits 0 — fixing is a convenience, never a gate; a failure
must not block the agent.

Unused imports (F401) are NOT removed here: an agent often adds an import in one edit and its
first use in the next, and removing it in between deletes the agent's work. The Stop hook
(`stop_gate.py`) removes them once the turn is over.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import env_python, file_path_from, read_event


def main() -> None:
    """Runs the hook."""
    event = read_event()
    path = file_path_from(event)
    if not path or not path.endswith(".py") or not os.path.isfile(path):
        return

    py = env_python()
    # Isolation point: never block on a fixer problem.
    with contextlib.suppress(Exception):
        subprocess.run(
            [
                py, "-m", "ruff", "check", "--fix", "--unfixable", "F401",
                "--quiet", "--exit-zero", path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )


if __name__ == "__main__":
    main()
