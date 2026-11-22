"""Server-Sent Events (SSE) streaming of live per-stage query progress.

The pipelines already emit structured events synchronously via
``src.observability.trace_context.log_event`` -> the ``xbrlrag.events`` logger
(``query_start``, ``decision``, ``llm_call``, ``numeric_lineage``, ``retry``,
``verifier_error``, ``query_summary``). We tap that logger with a handler,
run the query in a worker thread, and forward *that thread's* events to an SSE
stream in real time — no pipeline changes required.

Thread filtering: ``xbrlrag.events`` is a process-wide logger that also carries
events from any other concurrent query. Each request runs the pipeline in its
own thread and every event is emitted synchronously in that thread, so we filter
records by the worker thread's id (captured before the pipeline emits anything).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Optional

from src.api import pipelines
from src.logging_config import get_logger

logger = get_logger(__name__)

_EVENTS_LOGGER_NAME = "xbrlrag.events"


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


class _QueueHandler(logging.Handler):
    """Forward ``xbrlrag.events`` records emitted by *worker_ident* onto a queue."""

    def __init__(self, q: queue.Queue[dict], holder: dict):
        super().__init__(level=logging.INFO)
        self._q = q
        self._holder = holder  # {"ident": <worker thread id>} set before any emit

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._holder.get("ident"):
            return
        try:
            event = json.loads(record.getMessage())
        except Exception:
            return
        if isinstance(event, dict) and "kind" in event:
            self._q.put(event)


def event_stream(query: str, mode: str, user_id: str | None = None) -> Iterator[str]:
    """Yield SSE lines: live pipeline events, then a terminal ``result`` event.

    Always finishes with one ``{"kind": "result", ...}`` event carrying the
    answer dict + ``query_id`` (or an ``error``), then closes the stream.
    """
    q: queue.Queue[dict] = queue.Queue()
    holder: dict = {}
    result: dict = {}

    events_logger = logging.getLogger(_EVENTS_LOGGER_NAME)
    # Ensure INFO events are not filtered out before reaching our handler.
    prev_level = events_logger.level
    events_logger.setLevel(logging.INFO)
    handler = _QueueHandler(q, holder)
    events_logger.addHandler(handler)

    def run() -> None:
        holder["ident"] = threading.get_ident()
        try:
            if mode in ("auto", "xbrl"):
                answer, query_id = pipelines.run_xbrl(query, user_id=user_id)
            else:
                answer, query_id = pipelines.run_sec10k(query, mode)
            result.update({"query_id": query_id, "answer": answer, "mode": mode})
        except Exception as exc:  # surface as a clean terminal event
            logger.exception("stream query failed")
            result.update({"error": f"{type(exc).__name__}: {exc}", "answer": {}, "mode": mode})
        finally:
            q.put({"kind": "__done__"})

    worker = threading.Thread(target=run, name="xbrlrag-stream-query", daemon=True)
    worker.start()

    try:
        yield _sse({"kind": "stream_open", "mode": mode})
        while True:
            try:
                event = q.get(timeout=15.0)
            except queue.Empty:
                yield ": keepalive\n\n"  # comment line keeps the connection warm
                if not worker.is_alive() and q.empty():
                    break
                continue
            if event.get("kind") == "__done__":
                break
            yield _sse(event)
        yield _sse({"kind": "result", **result})
    finally:
        events_logger.removeHandler(handler)
        events_logger.setLevel(prev_level)
