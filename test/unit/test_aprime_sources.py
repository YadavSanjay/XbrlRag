"""Unit tests for the A' draw's EDGAR and beacon sources and its CLI guard.

The HTTP layer is replaced by a fake ``http_get`` (the I/O boundary); page parsing,
paging, caching and pacing run for real. No network. Spec: docs/specs/aprime-draw.md.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from src.eval.aprime.beacon_source import NistBeaconSource, epoch_ms
from src.eval.aprime.core import check_eligibility
from src.eval.aprime.edgar_source import EdgarSubmissionsSource, page_records

REPO = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "eval" / "aprime_draw.py"
_spec = importlib.util.spec_from_file_location("aprime_draw_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _page(rows):
    """A columnar submissions page from (form, filed, report, accession, inline) rows."""
    cols = ("form", "filingDate", "reportDate", "accessionNumber", "isInlineXBRL")
    return {c: [r[i] for r in rows] for i, c in enumerate(cols)}


class _FakeHttp:
    """Serves fixed bodies by URL and records each request."""

    def __init__(self, bodies: dict[str, tuple[int, bytes]]) -> None:
        self.bodies = bodies
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        return self.bodies[url]


BASE = "https://data.sec.gov/submissions/"
RECENT = _page([("10-K", "2025-07-30", "2025-06-30", "acc-25", 1),
                ("4", "2025-08-01", "", "acc-f4", 0),
                ("10-K", "2024-07-30", "2024-06-30", "acc-24", 1)])
OLDER = _page([("10-K", "2023-07-27", "2023-06-30", "acc-23", 1),
               ("10-K/A", "2023-09-01", "2023-06-30", "acc-23a", 0)])
MAIN = {"cik": "789019", "filings": {"recent": RECENT,
                                      "files": [{"name": "CIK0000789019-submissions-001.json"}]}}


def _http():
    return _FakeHttp({
        BASE + "CIK0000789019.json": (200, json.dumps(MAIN).encode()),
        BASE + "CIK0000789019-submissions-001.json": (200, json.dumps(OLDER).encode()),
    })


def test_page_records_maps_columns_and_inline_flag():
    recs = page_records(RECENT)
    assert recs[0] == {"form": "10-K", "filing_date": "2025-07-30", "report_date": "2025-06-30",
                       "accession_no": "acc-25", "is_inline_xbrl": True}
    assert recs[1]["is_inline_xbrl"] is False
    assert len(recs) == 3


def test_filings_merges_older_pages_and_sends_identity(tmp_path):
    http = _http()
    src = EdgarSubmissionsSource("Jane Doe jane@example.com", tmp_path, http_get=http,
                                 min_interval_s=0)
    recs = src.filings("789019")
    assert [r["accession_no"] for r in recs] == ["acc-25", "acc-f4", "acc-24", "acc-23",
                                                 "acc-23a"]
    assert all(h == {"User-Agent": "Jane Doe jane@example.com"} for _, h in http.calls)
    assert check_eligibility(recs)["eligible"] is True


def test_filings_rerun_is_served_from_cache(tmp_path):
    http = _http()
    EdgarSubmissionsSource("x y@z", tmp_path, http_get=http, min_interval_s=0).filings("789019")
    again = _FakeHttp({})
    recs = EdgarSubmissionsSource("x y@z", tmp_path, http_get=again).filings("789019")
    assert again.calls == []
    assert len(recs) == 5


def test_non_200_raises_and_caches_nothing(tmp_path):
    http = _FakeHttp({BASE + "CIK0000000001.json": (403, b"denied")})
    src = EdgarSubmissionsSource("x y@z", tmp_path, http_get=http, min_interval_s=0)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        src.filings("1")
    assert list(tmp_path.iterdir()) == []


def test_pacing_waits_between_fetches(tmp_path):
    slept: list[float] = []
    now = [100.0]
    src = EdgarSubmissionsSource("x y@z", tmp_path, http_get=_http(), min_interval_s=0.15,
                                 clock=lambda: now[0], sleep=slept.append)
    src.filings("789019")  # two fetches at the same clock reading
    assert slept == [pytest.approx(0.15)]  # float subtraction of clock readings


def test_epoch_ms_and_beacon_url():
    # 2026-10-27T12:00Z = 1793102400 s since the epoch (date -u -d ... +%s)
    assert epoch_ms("2026-10-27T12:00Z") == 1_793_102_400_000
    with pytest.raises(ValueError):
        epoch_ms("2026-10-27T12:00")
    pulse = {"timeStamp": "2026-10-27T12:00:00.000Z", "outputValue": "A" * 128}
    http = _FakeHttp({"https://beacon.nist.gov/beacon/2.0/pulse/time/1793102400000":
                      (200, json.dumps({"pulse": pulse}).encode())})
    assert NistBeaconSource(http_get=http).pulse("2026-10-27T12:00Z") == pulse


def test_beacon_errors():
    url = "https://beacon.nist.gov/beacon/2.0/pulse/time/1793102400000"
    with pytest.raises(RuntimeError, match="HTTP 404"):
        NistBeaconSource(http_get=_FakeHttp({url: (404, b"")})).pulse("2026-10-27T12:00Z")
    with pytest.raises(RuntimeError, match="no pulse"):
        NistBeaconSource(http_get=_FakeHttp({url: (200, b"{}")})).pulse("2026-10-27T12:00Z")


@pytest.mark.parametrize("when", ["2999-01-01T00:00Z", "2026-10-27T12:00"])
def test_draw_refuses_future_or_naive_pulse_time(tmp_path, capsys, when):
    pool = tmp_path / "pool.json"
    pool.write_text('{"candidates": []}', encoding="utf-8")
    code = cli.main(["draw", "--pool", str(pool), "--pulse-time", when,
                     "--out", str(tmp_path / "d.json")])
    assert code == 2
    assert "refusing" in capsys.readouterr().err
    assert not (tmp_path / "d.json").exists()
