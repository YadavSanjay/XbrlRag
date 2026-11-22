# The log-directory override: where a process's log files go when a held-out build must log
# into the held-out tree instead of the repository's logs/. ``configure_logging``
# (src/logging_config.py) applies it. Spec: docs/specs/hf-suite-build.md, requirement 11.

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LOG_DIR_ENV = "XBRLRAG_LOG_DIR"


def _absolute(path: Path) -> Path:
    """``path`` itself, refused when relative (it would be anchored at the project root)."""
    if not path.is_absolute():
        raise ValueError("the log directory must be absolute (a relative one would be "
                         "anchored at the project root)")
    return path


def resolve_log_dir(explicit: str | Path | None, environ: Mapping[str, str]) -> Path | None:
    """The directory log files are moved into, if any.

    Args:
        explicit: A directory passed by the caller, or None.
        environ: The environment; ``XBRLRAG_LOG_DIR`` is read when ``explicit`` is None.

    Returns:
        The directory, or None to keep the configured log files.

    Raises:
        ValueError: The directory is relative.
    """
    if explicit is not None:
        return _absolute(Path(explicit))
    value = environ.get(LOG_DIR_ENV)
    if value:
        return _absolute(Path(value))
    return None


def relocate_log_handlers(cfg: Mapping[str, Any], log_dir: str | Path) -> dict[str, Any]:
    """A copy of a logging config with every file handler's file moved into ``log_dir``.

    Args:
        cfg: A ``dictConfig`` mapping (not changed).
        log_dir: The absolute directory.

    Returns:
        The relocated copy; each ``filename`` keeps its name and placeholders.

    Raises:
        ValueError: ``log_dir`` is relative.
    """
    root = _absolute(Path(log_dir))
    out: dict[str, Any] = copy.deepcopy(dict(cfg))
    for handler in (out.get("handlers") or {}).values():
        if isinstance(handler, dict) and "filename" in handler:
            handler["filename"] = str(root / Path(str(handler["filename"])).name)
    return out
