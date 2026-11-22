"""Starts the H-X filing locator on http://127.0.0.1:8765 (local only, no access log).

Usage::

    conda run -n py312 --no-capture-output python -m tools.hx_locator [--port 8765]
"""

from __future__ import annotations

import argparse
import contextlib
import logging


def _drop_log_files() -> None:
    """Closes every file handler and deletes this process's log file.

    Importing ``src`` configures the repository's logging, which opens
    ``logs/xbrlrag_<process>_<pid>.log``. Even empty, that file would record that the
    locator ran and when, and an error would be written into it; the locator logs to the
    console only.
    """
    from src.logging_config import current_log_file

    loggers = [logging.getLogger()]
    loggers += [lg for lg in logging.root.manager.loggerDict.values()
                if isinstance(lg, logging.Logger)]
    for lg in loggers:
        for handler in list(lg.handlers):
            if isinstance(handler, logging.FileHandler):
                lg.removeHandler(handler)
                handler.close()
    path = current_log_file()
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink()


def main() -> None:
    """Parses the port, quiets logging and serves the app on localhost."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    from tools.hx_locator.app import create_app

    # What you browse is yours: no log file, no access log, console logs only at WARNING.
    _drop_log_files()
    logging.getLogger().setLevel(logging.WARNING)
    for name in ("src", "tools", "uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(logging.WARNING)
    print(f"H-X filing locator: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False,
                log_level="warning")


if __name__ == "__main__":
    main()
