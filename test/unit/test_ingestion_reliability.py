"""Tests for §4.8 reliability: Arelle timeout retry + SEC downloader retry."""
from pathlib import Path

import pytest

from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import ArelleXBRLIngestor


def _ingestor(max_attempts=3):
    # retry_backoff=0 => instant backoff; persist_audit=False => no DB.
    return ArelleXBRLIngestor(
        arelle_cmd="arelle",
        sec_root=Path("."),
        db_conn_params={},
        persist_audit=False,
        max_attempts=max_attempts,
        retry_backoff=0.0,
    )


def _stub_run_arelle(ing, results):
    """Make _run_arelle return queued (rc, stdout, stderr) tuples in order."""
    seq = list(results)
    calls = {"n": 0}

    def fake(htm_path):
        out = seq[calls["n"]]
        calls["n"] += 1
        return out

    ing._run_arelle = fake  # type: ignore[assignment]
    return calls


# ----------------------------------------------------------------------
# Arelle timeout retry
# ----------------------------------------------------------------------

def test_timeout_then_success_retries():
    ing = _ingestor(max_attempts=3)
    calls = _stub_run_arelle(ing, [(-1, "", "Timeout after 600s"), (0, "ok", "")])
    rc, _out, _err, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == 0
    assert attempts == 2
    assert calls["n"] == 2


def test_persistent_timeout_exhausts_attempts():
    ing = _ingestor(max_attempts=3)
    _stub_run_arelle(ing, [(-1, "", "t"), (-1, "", "t"), (-1, "", "t")])
    rc, _o, _e, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == -1
    assert attempts == 3  # tried max_attempts times


def test_validation_error_is_not_retried():
    ing = _ingestor(max_attempts=3)
    calls = _stub_run_arelle(ing, [(1, "", "validation failed"), (0, "", "")])
    rc, _o, _e, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == 1          # permanent — returned on first attempt
    assert attempts == 1
    assert calls["n"] == 1  # second (success) result never consumed


def test_arelle_not_found_is_not_retried():
    ing = _ingestor(max_attempts=3)
    _stub_run_arelle(ing, [(-2, "", "not found"), (0, "", "")])
    rc, _o, _e, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == -2
    assert attempts == 1


def test_success_first_try():
    ing = _ingestor(max_attempts=3)
    _stub_run_arelle(ing, [(0, "ok", "")])
    rc, _o, _e, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == 0
    assert attempts == 1


def test_max_attempts_one_disables_retry():
    ing = _ingestor(max_attempts=1)
    _stub_run_arelle(ing, [(-1, "", "t"), (0, "", "")])
    rc, _o, _e, attempts = ing._run_arelle_with_retry(Path("x.htm"))
    assert rc == -1
    assert attempts == 1


# ----------------------------------------------------------------------
# SEC EDGAR downloader retry
# ----------------------------------------------------------------------

@pytest.fixture
def downloader(tmp_path):
    from src.ingest.sec_edgar.downloader.sec_filing_downloader import SECFilingDownloader

    return SECFilingDownloader(
        output_root=tmp_path, identity="Test test@example.com", max_attempts=3, retry_backoff=0.0
    )


def test_downloader_retries_transient(downloader):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("network blip")
        return "filing"

    assert downloader._retrying(fn, "edgar query") == "filing"
    assert calls["n"] == 3


def test_downloader_does_not_retry_missing_filing(downloader):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise LookupError("No 10-K matched")

    with pytest.raises(LookupError):
        downloader._retrying(fn, "edgar query")
    assert calls["n"] == 1  # permanent — failed fast
