"""`pipelines_importable` must not report an intentional build as a defect.

The serving and ingest images are deliberately torch-free, so the PDF-legacy
sec10k pipeline (FlagEmbedding/torch) cannot import there — its query modes are
simply unavailable. Logging that at WARNING made every container start emit

    WARNING sec10k pipeline import failed: No module named 'FlagEmbedding'

which reads as a broken deployment. A missing *first-party* module is a real
packaging error and must stay at WARNING.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from src.api import pipelines

MODULE_LOGGER = "src.api.pipelines"


@contextmanager
def _capture(level: int = logging.INFO):
    """Collect records off the module's own logger.

    Not `caplog`: configs/logging.yaml sets propagate=False, so pytest's
    root-attached handler never sees these.
    """
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    log = logging.getLogger(MODULE_LOGGER)
    handler = _Sink(level)
    previous = log.level
    log.addHandler(handler)
    log.setLevel(level)
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)


def _probe(monkeypatch, exc: BaseException) -> list[logging.LogRecord]:
    def _raise(_module):
        raise exc

    monkeypatch.setattr(pipelines.importlib, "import_module", _raise)
    status: dict = {}
    with _capture() as records:
        pipelines._probe_pipeline("sec10k", "src.pipelines.sec10k.x", status)
    assert status["sec10k"] is False
    return records


def test_missing_third_party_dep_is_informational(monkeypatch):
    """The reported case: FlagEmbedding absent in a torch-free image."""
    records = _probe(monkeypatch, ModuleNotFoundError(name="FlagEmbedding"))

    assert [r.levelno for r in records] == [logging.INFO]
    message = records[0].getMessage()
    assert "FlagEmbedding" in message
    assert "unavailable" in message
    assert "failed" not in message


def test_missing_first_party_module_is_a_warning(monkeypatch):
    """A missing src.* module is a genuine packaging error, not a build choice."""
    records = _probe(
        monkeypatch, ModuleNotFoundError(name="src.pipelines.sec10k.retrieval_pipeline")
    )

    assert [r.levelno for r in records] == [logging.WARNING]
    assert "failed" in records[0].getMessage()


def test_other_exceptions_stay_warnings(monkeypatch):
    records = _probe(monkeypatch, RuntimeError("boom"))

    assert [r.levelno for r in records] == [logging.WARNING]
    assert "boom" in records[0].getMessage()


def test_successful_import_is_silent(monkeypatch):
    monkeypatch.setattr(pipelines.importlib, "import_module", lambda _m: object())
    status: dict = {}
    with _capture() as records:
        pipelines._probe_pipeline(
            "xbrl", "src.pipelines.xbrl.retrieval_pipeline", status
        )

    assert status["xbrl"] is True
    assert records == []


# Deliberately NOT tested here: calling the real `pipelines_importable()`. It
# imports the actual XBRL pipeline, which installs a process-global audit sink and
# breaks test_ingestion_audit / test_audit_store later in the same session (both
# passed alone, failed in a full run). The four tests above cover the logic via
# `_probe_pipeline`; asserting "returns two bools" was not worth ~50s and a
# cross-test side effect.
