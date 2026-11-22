"""Locating the Arelle CLI, consistently, across platforms.

``ARELLE_CMD`` takes two legitimate shapes and every caller must accept both:

* **Windows dev box** — an absolute path to the frozen build
  (``<Arelle install dir>/arelleCmdLine.exe``).
* **Linux / the ingest container** — a bare console-script name
  (``arelleCmdLine``), because ``arelle-release`` installs it on ``PATH``.

Mixing those up is a recurring bug class here: code that validates with
``Path(cmd).exists()`` works on Windows and fails on Linux, while
``subprocess`` itself happily PATH-resolves a bare name. That divergence means
one stage of the pipeline runs and the next one claims Arelle is missing.
Route every check through :func:`resolve_arelle_cmd` so they agree.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# The frozen Windows build's executable, resolved on PATH; set ARELLE_CMD to its
# absolute path when its folder is not on PATH. Elsewhere the pip `arelle-release`
# console script is on PATH (and is what Dockerfile.ingest bakes).
_WINDOWS_DEFAULT = "arelleCmdLine.exe"
_POSIX_DEFAULT = "arelleCmdLine"


def default_arelle_cmd() -> str:
    """Platform-appropriate fallback when ``ARELLE_CMD`` is unset.

    A hardcoded ``D:/...`` default on Linux produces a nonsense error message
    pointing at a drive letter that cannot exist.
    """
    return _WINDOWS_DEFAULT if sys.platform == "win32" else _POSIX_DEFAULT


def arelle_cmd_from_env(override: str | None = None) -> str:
    """Pick the Arelle command: explicit override > ``ARELLE_CMD`` > platform default.

    Treats an empty ``ARELLE_CMD`` as unset — an env file with ``ARELLE_CMD=``
    should fall back, not resolve the empty string.
    """
    return override or os.environ.get("ARELLE_CMD") or default_arelle_cmd()


def resolve_arelle_cmd(arelle_cmd: str) -> str:
    """Resolve *arelle_cmd* to something runnable, PATH lookup included.

    ``shutil.which`` handles both a bare name on PATH and an absolute path. The
    literal-existence fallback keeps a file that exists but is not marked
    executable working, matching the historical behaviour on Windows.

    Raises
    ------
    FileNotFoundError
        If the command is neither on PATH nor an existing file. The message names
        the value that failed, since "arelleCmdLine not found" is otherwise
        indistinguishable between "PATH is wrong" and "ARELLE_CMD is wrong".
    """
    resolved = shutil.which(arelle_cmd)
    if resolved is not None:
        return resolved
    if Path(arelle_cmd).exists():
        return arelle_cmd
    raise FileNotFoundError(
        f"arelleCmdLine not found: {arelle_cmd!r}. Set ARELLE_CMD to an absolute "
        "path or to a name on PATH (the ingest image bakes ARELLE_CMD=arelleCmdLine), "
        "or pass arelle_cmd explicitly."
    )
