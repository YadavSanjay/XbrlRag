"""Arelle subprocess handling: secret masking, argv construction, streaming.

Context: an AWS ingest stall was undiagnosable because ``subprocess.run(
capture_output=True)`` swallowed Arelle's log for the whole 600s timeout and then
discarded it, returning a bare "Timeout after 600s". Three different root causes
(cold web cache, an unreachable plain-HTTP schema host, a missing pg8000 driver)
all presented identically. These tests pin the behaviour that made them legible —
plus the redaction that keeps the DB password out of logs, since it travels as a
positional field of Arelle's ``--store-to-XBRL-DB`` argument.

Arelle itself is never invoked: ``_arelle_argv`` is stubbed with a short Python
command so the streaming/timeout paths run against a real subprocess.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor as mod
from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import (
    ArelleXBRLIngestor,
    _mask_secret,
)

MODULE_LOGGER = "src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor"

# Deliberately full of shell/regex metacharacters: masking is a plain str.replace,
# and a password like this is exactly what an RDS-generated secret looks like.
SECRET = "s3cr3t)(:pa$$?w0rd"

CONN = {
    "host": "db.example.invalid",
    "port": 5432,
    "user": "arag_admin",
    "password": SECRET,
    "dbname": "xbrl_db",
}


def _ingestor(**kw) -> ArelleXBRLIngestor:
    params: dict = {
        "arelle_cmd": "arelleCmdLine",
        "sec_root": Path("."),
        "db_conn_params": dict(CONN),
        "persist_audit": False,
    }
    params.update(kw)
    return ArelleXBRLIngestor(**params)


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


@contextmanager
def _capture_logs(level: int = logging.INFO):
    """Collect records from the ingestor's own logger.

    Not ``caplog``: ``configs/logging.yaml`` sets ``propagate=False`` on the
    project loggers, so pytest's root-attached capture handler never sees these
    records even though they reach the console.
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


# --------------------------------------------------------------------------
# secret masking
# --------------------------------------------------------------------------
@pytest.mark.parametrize("secret", ["", None])
def test_mask_secret_is_noop_for_empty(secret):
    # str.replace("") would splice the marker between every character.
    assert _mask_secret("abc", secret) == "abc"


def test_mask_secret_replaces_every_occurrence():
    masked = _mask_secret(f"a {SECRET} b {SECRET}", SECRET)
    assert SECRET not in masked
    assert masked.count("***") == 2


def test_cmd_log_does_not_leak_the_password(tmp_path):
    """The argv genuinely contains the password; the log line must not."""
    ing = _ingestor(arelle_cmd="arag-no-such-binary")
    with _capture_logs() as records:
        rc, _, _ = ing._run_arelle(tmp_path / "a.htm")

    assert rc == -2  # missing executable is permanent, not retried
    logged = "\n".join(r.getMessage() for r in records)
    assert "CMD:" in logged
    assert SECRET not in logged
    assert "***" in logged


def test_connect_db_error_redacts_the_password(monkeypatch):
    def _boom(**_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.psycopg2, "connect", _boom)
    with pytest.raises(RuntimeError) as excinfo:
        _ingestor()._connect_db()

    message = str(excinfo.value)
    assert SECRET not in message
    assert "***" in message
    assert "arag_admin" in message  # non-secret params stay, they aid diagnosis


# --------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------
def test_argv_carries_product_type_and_plugin_order():
    argv = _ingestor()._arelle_argv(Path("/filings/a.htm"))

    # Without the trailing ",postgres" xbrlDB HTTP-probes the port and hangs.
    conn_str = argv[argv.index("--store-to-XBRL-DB") + 1]
    assert conn_str.endswith(",postgres")

    # The iXBRL shim must load BEFORE xbrlDB or insertFacts() raises.
    plugins = argv[argv.index("--plugins") + 1]
    before, _, after = plugins.partition("|")
    assert "xbrl_db_ixbrl_patch.py" in before
    assert after == "EDGAR/transform|xbrlDB"

    assert "--internetConnectivity" not in argv  # unset by default


def test_argv_passes_internet_connectivity_when_set():
    argv = _ingestor(internet_connectivity="offline")._arelle_argv(Path("/f/a.htm"))
    assert argv[argv.index("--internetConnectivity") + 1] == "offline"


# --------------------------------------------------------------------------
# streaming / timeout
# --------------------------------------------------------------------------
def test_run_arelle_captures_both_streams(monkeypatch, tmp_path):
    ing = _ingestor()
    monkeypatch.setattr(
        ing,
        "_arelle_argv",
        lambda _p: _py(
            "import sys; print('on-stdout'); print('on-stderr', file=sys.stderr)"
        ),
    )
    rc, out, err = ing._run_arelle(tmp_path / "a.htm")

    assert rc == 0
    assert "on-stdout" in out
    assert "on-stderr" in err


def test_timeout_reports_arelle_last_output(monkeypatch, tmp_path):
    """The regression that mattered: a timeout must say what Arelle was doing."""
    ing = _ingestor(arelle_timeout=2, heartbeat_secs=0)
    monkeypatch.setattr(
        ing,
        "_arelle_argv",
        lambda _p: _py(
            "import time; print('resolving DTS', flush=True); time.sleep(60)"
        ),
    )
    rc, out, err = ing._run_arelle(tmp_path / "a.htm")

    assert rc == -1  # classified 'timeout' => retryable
    assert "resolving DTS" in out
    assert "resolving DTS" in err  # surfaced in the failure message, not just stdout
    assert "Timeout after 2s" in err


def test_heartbeat_reports_progress_while_running(monkeypatch, tmp_path):
    ing = _ingestor(arelle_timeout=30, heartbeat_secs=1)
    monkeypatch.setattr(
        ing,
        "_arelle_argv",
        lambda _p: _py("import time; print('phase-1', flush=True); time.sleep(3)"),
    )
    with _capture_logs() as records:
        rc, _, _ = ing._run_arelle(tmp_path / "a.htm")

    assert rc == 0
    beats = [r.getMessage() for r in records if "still running" in r.getMessage()]
    assert beats, "expected at least one heartbeat during a 3s run"
    assert "phase-1" in beats[-1]


def test_nonzero_exit_is_returned_verbatim(monkeypatch, tmp_path):
    ing = _ingestor()
    monkeypatch.setattr(ing, "_arelle_argv", lambda _p: _py("import sys; sys.exit(3)"))
    rc, _, _ = ing._run_arelle(tmp_path / "a.htm")
    assert rc == 3  # validation errors are permanent; must not be masked as -1


# --------------------------------------------------------------------------
# every Arelle caller shares the same knobs
# --------------------------------------------------------------------------
def test_connectivity_args():
    from src.ingest.util.arelle_run import connectivity_args

    assert connectivity_args(None) == []
    assert connectivity_args("") == []
    assert connectivity_args("offline") == ["--internetConnectivity", "offline"]


def test_label_exporters_honour_the_same_env_as_the_fact_ingest(monkeypatch):
    """The label/dimension exporters spawn their own Arelle.

    They used to hardcode a 600s timeout and never pass --internetConnectivity,
    so `-e ARELLE_TIMEOUT=… -e ARELLE_INTERNET_CONNECTIVITY=offline` silently did
    nothing for them and a label export could stall on an unreachable host after
    the fact ingest had been told to work from cache only.
    """
    from src.ingest.sec_edgar.ingest.concept_labels_populator import (
        ConceptLabelsPopulator,
        DimensionMembersPopulator,
    )

    monkeypatch.setenv("ARELLE_TIMEOUT", "1234")
    monkeypatch.setenv("ARELLE_HEARTBEAT_SECS", "7")
    monkeypatch.setenv("ARELLE_INTERNET_CONNECTIVITY", "offline")

    for cls in (ConceptLabelsPopulator, DimensionMembersPopulator):
        populator = cls.from_env()
        assert populator.arelle_timeout == 1234, cls.__name__
        assert populator.heartbeat_secs == 7, cls.__name__
        assert populator.internet_connectivity == "offline", cls.__name__
