"""Project-level logging configuration helpers.

Usage:
    from src.logging_config import configure_logging, get_logger
    configure_logging()  # call once at program start
    logger = get_logger(__name__)

This loads `configs/logging.yaml` by default. Override with LOG_LEVEL env var
to change the runtime level (e.g. DEBUG).
"""
from __future__ import annotations

import fnmatch
import json
import logging
import logging.config
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - instructive
    yaml = None


_CONTEXT_NONE = "-"


class LogContextFilter(logging.Filter):
    """Stamp every record with the active query's correlation id and stage.

    Reads the per-query trace context (set by ``src.observability.trace_context``)
    via ``contextvars`` and attaches ``query_id`` / ``stage`` to the record, so
    format strings and the JSON formatter can reference them. Both default to
    ``"-"`` when no trace is active, so non-XBRL logs never break.

    The trace module is imported lazily here to avoid an import cycle
    (``trace_context`` imports :func:`get_logger` from this module).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            from src.observability.trace_context import current_query_id, current_stage

            record.query_id = current_query_id()
            record.stage = current_stage()
        except Exception:  # pragma: no cover - defensive; telemetry must not break logging
            record.query_id = getattr(record, "query_id", _CONTEXT_NONE)
            record.stage = getattr(record, "stage", _CONTEXT_NONE)
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line (machine-parseable logs).

    Includes the correlation fields injected by :class:`LogContextFilter`. Use
    via ``configs/logging.yaml`` on the file handler; the console can stay
    human-readable text.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "query_id": getattr(record, "query_id", _CONTEXT_NONE),
            "stage": getattr(record, "stage", _CONTEXT_NONE),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _default_config_path() -> Path:
    # project root is two parents up from this file (src/)
    return Path(__file__).resolve().parents[1] / "configs" / "logging.yaml"


# Per-process log files (docs/specs/per-process-log-file.md).
_LOG_PRUNE_PATTERN = "xbrlrag_*.log*"
_LOG_MAX_AGE_SECONDS = 7 * 24 * 3600
_current_log_file: Path | None = None


def _project_root() -> Path:
    """The project root, as ``src.PROJECT_ROOT`` defines it.

    Read here rather than imported: ``src/__init__.py`` configures logging before it
    loads ``.env`` and mirrors the legacy ``ARAG_*`` names, so the legacy name is
    honoured directly.
    """
    root = os.environ.get("XBRLRAG_PROJECT_ROOT") or os.environ.get("ARAG_PROJECT_ROOT")
    return Path(root) if root else Path(__file__).resolve().parents[1]


def _per_process_filenames(cfg: dict) -> Path | None:
    """Give each file handler in ``cfg`` this process's own file, in place.

    Expands ``{process}`` / ``{pid}``, anchors relative names at the project root,
    creates the folder and prunes stale log files there. Every process rotating its
    own file is what keeps Windows rotation from failing on a file another process
    holds open.

    Returns:
        The first handler's resolved file, for ``current_log_file()``.
    """
    import sys
    import time

    first: Path | None = None
    process = process_name(sys.argv)
    for handler in (cfg.get("handlers") or {}).values():
        template = handler.get("filename")
        if not template:
            continue
        path = expand_log_filename(str(template), _project_root(), process, os.getpid())
        path.parent.mkdir(parents=True, exist_ok=True)
        _prune_stale_logs(path.parent, time.time())
        handler["filename"] = str(path)
        first = first or path
    return first


def _prune_stale_logs(folder: Path, now: float) -> None:
    """Delete stale per-process log files; never fails logging setup."""
    try:
        entries = [(p, p.stat().st_mtime) for p in folder.iterdir() if p.is_file()]
    except OSError:
        return
    for path in stale_log_files(entries, now, _LOG_MAX_AGE_SECONDS, _LOG_PRUNE_PATTERN):
        try:
            path.unlink()
        except OSError:
            pass  # open in another process, or already gone: try again next start


def current_log_file() -> Path | None:
    """This process's log file, once ``configure_logging`` has set it up."""
    return _current_log_file


def configure_logging(config_path: str | Path | None = None, default_level: int = logging.INFO,
                      log_dir: str | Path | None = None) -> None:
    """Configure logging for the project.

    Args:
        config_path: path to YAML logging config. If None, uses `configs/logging.yaml`.
        default_level: fallback level if config file missing.
        log_dir: An absolute directory every file handler's file is moved into; None reads
            ``XBRLRAG_LOG_DIR``, and with neither the configured files are kept
            (docs/specs/hf-suite-build.md, requirement 11).

    Raises:
        ValueError: The log directory is relative.
    """
    global _current_log_file
    from src.log_dir import relocate_log_handlers, resolve_log_dir

    target_dir = resolve_log_dir(log_dir, os.environ)

    if config_path is None:
        config_path = _default_config_path()
    else:
        config_path = Path(config_path)

    env_level = os.getenv("LOG_LEVEL")

    if config_path.exists() and yaml is not None:
        with open(config_path, encoding="utf8") as f:
            cfg = yaml.safe_load(f)

        if target_dir is not None:
            cfg = relocate_log_handlers(cfg, target_dir)
        _current_log_file = _per_process_filenames(cfg)
        # If user passed LOG_LEVEL env var, apply it to root logger after loading
        logging.config.dictConfig(cfg)

        if env_level:
            try:
                level_val = getattr(logging, env_level.upper())
            except Exception:
                level_val = default_level
            logging.getLogger().setLevel(level_val)
    else:
        # Fallback to basicConfig when YAML not present or PyYAML missing
        logging.basicConfig(level=default_level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
        if env_level:
            try:
                level_val = getattr(logging, env_level.upper())
                logging.getLogger().setLevel(level_val)
            except Exception:
                pass


def process_name(argv: Sequence[str]) -> str:
    """The entrypoint's name for a per-process log file (docs/specs/per-process-log-file.md).

    Args:
        argv: The process's ``sys.argv``.

    Returns:
        A non-empty name matching ``[a-z0-9_-]+``.
    """
    if not argv or argv[0] in ("", "-c", "-m"):
        return "python"
    # Split on both separators by hand: pathlib would read a Windows path as one name
    # on POSIX, and drops a trailing separator.
    parts = re.split(r"[\\/]", argv[0])
    stem = parts[-1].rsplit(".", 1)[0] if "." in parts[-1] else parts[-1]
    if stem == "__main__" and len(parts) > 1:
        stem = parts[-2]  # python -m pkg: name the package
    name = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_")
    return name or "python"


def expand_log_filename(template: str, root: Path, process: str, pid: int) -> Path:
    """Expand a log filename template and anchor it at ``root``.

    Args:
        template: A filename that may contain ``{process}`` and ``{pid}``.
        root: The directory a relative result is resolved against.
        process: The entrypoint's name.
        pid: The process id.

    Returns:
        The log file's path.
    """
    path = Path(template.replace("{process}", process).replace("{pid}", str(pid)))
    return path if path.is_absolute() else root / path


def stale_log_files(
    entries: Iterable[tuple[Path, float]],
    now: float,
    max_age_seconds: float,
    pattern: str,
) -> list[Path]:
    """The log files old enough to prune.

    Args:
        entries: ``(path, mtime)`` pairs.
        now: The current time, in seconds since the epoch.
        max_age_seconds: The age beyond which a file is stale.
        pattern: An ``fnmatch`` glob the file name must match.

    Returns:
        The stale paths, in input order.
    """
    return [
        path
        for path, mtime in entries
        if fnmatch.fnmatch(path.name, pattern) and now - mtime > max_age_seconds
    ]


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger for a module. Use module __name__.

    Example:
        logger = get_logger(__name__)
    """
    return logging.getLogger(name or "")
