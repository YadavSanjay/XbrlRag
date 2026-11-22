"""Run ``arelleCmdLine`` as a subprocess with visible progress.

Arelle goes silent for long stretches — DTS resolution, then (for the fact
ingest) a whole taxonomy insert into Postgres. Buffering its output to
completion and throwing it away on timeout is what made a three-cause AWS stall
present as one opaque ``Timeout after 600s``: a cold web cache, an unreachable
plain-HTTP schema host, and a missing pg8000 driver were indistinguishable.

Every Arelle subprocess in this repo goes through :func:`run_arelle` so that a
stall is always diagnosable:

* both pipes are drained on their own threads (neither can fill and wedge Arelle);
* each line is logged at DEBUG as it arrives;
* an INFO heartbeat reports elapsed time and the last line seen;
* a timeout returns the retained tail, not just a duration.

Return-code convention, shared by all callers:
``-1`` timeout (transient, retryable) · ``-2`` executable not found ·
``-3`` other subprocess failure · otherwise Arelle's own exit code.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Sequence
from typing import IO, Deque, Optional, Tuple

# Enough trailing output to see what Arelle was doing when it stalled, without
# holding a full DTS-download log in memory.
_TAIL_LINES = 200

# Granularity of the wait/heartbeat loop.
_POLL_SECS = 1.0

REDACTED = "***"

# Return codes (see module docstring).
RC_TIMEOUT = -1
RC_NOT_FOUND = -2
RC_SUBPROCESS_ERROR = -3


def mask_secret(text: str, secret: str | None) -> str:
    """Replace *secret* with ``***`` in *text*.

    The DB password is a positional field of Arelle's ``--store-to-XBRL-DB``
    connection string, so it lands in argv and would otherwise be logged
    verbatim. Empty/None secrets are a no-op (``str.replace("")`` would splice
    the marker between every character).
    """
    if not secret:
        return text
    return text.replace(secret, REDACTED)


def connectivity_args(internet_connectivity: str | None) -> list[str]:
    """``--internetConnectivity`` args, or none when unset.

    ``offline`` forces cache-only DTS resolution, turning a cache miss into an
    immediate, legible failure instead of a stall against an unreachable host.
    """
    if not internet_connectivity:
        return []
    return ["--internetConnectivity", internet_connectivity]


def _pump(
    stream: IO[str] | None, tail: deque[str], label: str, log: logging.Logger
) -> None:
    """Drain *stream* line-by-line into *tail*, logging each line at DEBUG."""
    if stream is None:
        return
    try:
        for line in stream:
            stripped = line.rstrip("\r\n")
            tail.append(stripped)
            log.debug("[arelle:%s] %s", label, stripped)
    except (ValueError, OSError):
        # Pipe closed under us when the process was killed — expected.
        pass
    finally:
        try:
            stream.close()
        except (ValueError, OSError):
            pass


def _wait_with_heartbeat(
    proc: subprocess.Popen[str],
    timeout: float,
    heartbeat_secs: float,
    label: str,
    tail: deque[str],
    log: logging.Logger,
) -> int:
    """Wait for *proc*; raise ``subprocess.TimeoutExpired`` once *timeout* elapses."""
    start = time.monotonic()
    next_beat = start + heartbeat_secs if heartbeat_secs else None
    while True:
        try:
            return proc.wait(timeout=_POLL_SECS)
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now - start >= timeout:
                raise
            if next_beat is not None and now >= next_beat:
                log.info(
                    "Arelle still running on %s: %.0fs elapsed; last output: %s",
                    label,
                    now - start,
                    tail[-1] if tail else "(none yet)",
                )
                next_beat = now + heartbeat_secs


def run_arelle(
    argv: Sequence[str],
    *,
    timeout: float,
    logger: logging.Logger,
    label: str = "",
    heartbeat_secs: float = 60.0,
    secret: str | None = None,
    log_command: bool = True,
) -> tuple[int, str, str]:
    """Invoke arelleCmdLine, streaming its output. Returns ``(rc, stdout, stderr)``.

    *secret* is masked out of the logged command line. *label* names the work
    item in heartbeats (a filing name reads better than a full argv).
    """
    if log_command:
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in argv)
        logger.info("CMD: %s", mask_secret(cmd_str, secret))

    try:
        proc = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return RC_NOT_FOUND, "", f"arelleCmdLine not found: {argv[0]}"
    except Exception as exc:
        return RC_SUBPROCESS_ERROR, "", str(exc)

    out_tail: deque[str] = deque(maxlen=_TAIL_LINES)
    err_tail: deque[str] = deque(maxlen=_TAIL_LINES)
    pumps = [
        threading.Thread(
            target=_pump, args=(proc.stdout, out_tail, "out", logger), daemon=True
        ),
        threading.Thread(
            target=_pump, args=(proc.stderr, err_tail, "err", logger), daemon=True
        ),
    ]
    for t in pumps:
        t.start()

    try:
        rc = _wait_with_heartbeat(
            proc, timeout, heartbeat_secs, label or argv[0], out_tail, logger
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for t in pumps:
            t.join(timeout=5)
        # Arelle logs to stderr under --logFile logToStdErr and to stdout
        # otherwise, so report whichever actually carried output.
        captured = "\n".join(out_tail) or "\n".join(err_tail)
        return (
            RC_TIMEOUT,
            "\n".join(out_tail),
            f"Timeout after {timeout:g}s. Last Arelle output:\n"
            f"{captured or '(no output)'}",
        )
    except Exception as exc:
        proc.kill()
        proc.wait()
        return RC_SUBPROCESS_ERROR, "\n".join(out_tail), str(exc)

    for t in pumps:
        t.join(timeout=5)
    return rc, "\n".join(out_tail), "\n".join(err_tail)
