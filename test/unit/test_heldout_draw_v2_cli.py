"""Unit tests for the ``pool-v2`` and ``draw-v2`` subcommands of ``aprime_draw.py``.

The I/O boundaries are faked: the corpus-CIK query (Postgres), the NIST beacon and the EDGAR
filing indexes (network). The v2 pool, ranking and draw run for real.
Spec: docs/specs/heldout-draw-v2.md, requirement 6.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from src.eval.aprime.base import FilingIndexSource

REPO = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "eval" / "aprime_draw.py"
_spec = importlib.util.spec_from_file_location("aprime_draw_cli_v2", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

CONSTITUENTS_CSV = (
    "symbol,name,sector\n"
    "SWA,Soft A,Information Technology\n"
    "SWB,Soft B,Communication Services\n"
    "DCA,Drug A,Health Care\n"
    "DCB,Shop B,Consumer Staples\n"
    "UTL,Power,Utilities\n"
    "CORP,Corpus Co,Industrials\n"
    "WAVE,Wave Co,Energy\n"
)
TICKERS = {str(i): {"cik_str": cik, "ticker": t, "title": t} for i, (t, cik) in enumerate([
    ("SWA", 101), ("SWB", 102), ("DCA", 201), ("DCB", 202), ("UTL", 301), ("CORP", 401),
    ("WAVE", 501)])}
LISTS_YAML = "wave1: [WAVE]\nwave2: [ZZW]\nleft_out: [ZZL]\n"
PULSE_TIME = "2026-01-05T16:00Z"
PULSE = {"timeStamp": "2026-01-05T16:00:00.000Z", "outputValue": "ab" * 64}


def _eligible() -> list[dict]:
    return [{"form": "10-K", "filing_date": f"{y + 1}-02-01", "report_date": f"{y}-12-31",
             "accession_no": f"acc-{y}", "is_inline_xbrl": True} for y in (2023, 2024, 2025)]


class _FakeFilings(FilingIndexSource):
    """Every CIK is eligible; records each call."""

    def __init__(self, *_args: object) -> None:
        self.calls: list[str] = []

    def filings(self, cik: str) -> list[dict]:
        self.calls.append(cik)
        return _eligible()


class _FakeBeacon:
    def pulse(self, timestamp_utc: str) -> dict:
        assert timestamp_utc == PULSE_TIME
        return dict(PULSE)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    """Writes the inputs and fakes the corpus query."""
    cons = tmp_path / "constituents.csv"
    cons.write_text(CONSTITUENTS_CSV, encoding="utf-8")
    tickers = tmp_path / "company_tickers.json"
    tickers.write_text(json.dumps(TICKERS), encoding="utf-8")
    lists = tmp_path / "lists.yaml"
    lists.write_text(LISTS_YAML, encoding="utf-8")
    monkeypatch.setattr(cli, "corpus_ciks", lambda: ["0000000401"])
    return cons, tickers, lists


def _pool_args(tmp_path, inputs, out_name="pool.json"):
    cons, tickers, lists = inputs
    return ["pool-v2", "--constituents", str(cons), "--source", "test list",
            "--out", str(tmp_path / out_name), "--tickers-json", str(tickers),
            "--exclusions", str(lists)]


def test_pool_v2_writes_scheme_hashes_and_exclusions(tmp_path, inputs, capsys):
    cons, tickers, lists = inputs
    assert cli.main(_pool_args(tmp_path, inputs)) == 0
    data = (tmp_path / "pool.json").read_bytes()
    pool = json.loads(data)
    assert pool["scheme"] == "xbrlrag-heldout-v2"
    assert pool["source"] == "test list"
    assert pool["constituents_sha256"] == hashlib.sha256(cons.read_bytes()).hexdigest()
    assert pool["tickers_sha256"] == hashlib.sha256(tickers.read_bytes()).hexdigest()
    assert pool["exclusions_sha256"] == hashlib.sha256(lists.read_bytes()).hexdigest()
    assert pool["corpus_ciks"] == ["0000000401"]
    assert {e["symbol"]: e["reason"] for e in pool["excluded"]} == {
        "UTL": "sector", "CORP": "corpus", "WAVE": "wave1"}
    assert [c["symbol"] for c in pool["candidates"]] == ["SWA", "SWB", "DCA", "DCB"]
    out = capsys.readouterr().out
    assert "warning: left_out symbol ZZL" in out
    assert "warning: wave2 symbol ZZW" in out
    assert f"{hashlib.sha256(data).hexdigest()}  pool.json" in out


def test_pool_v2_is_byte_reproducible(tmp_path, inputs):
    assert cli.main(_pool_args(tmp_path, inputs, "a.json")) == 0
    assert cli.main(_pool_args(tmp_path, inputs, "b.json")) == 0
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_pool_v2_defaults_to_committed_lists(tmp_path, inputs):
    args = _pool_args(tmp_path, inputs)[:-2]  # drop --exclusions
    assert cli.main(args) == 0
    pool = json.loads((tmp_path / "pool.json").read_bytes())
    committed = REPO / "configs" / "eval" / "v2" / "heldout_exclusions.yaml"
    assert pool["exclusions_sha256"] == hashlib.sha256(committed.read_bytes()).hexdigest()


def test_draw_v2_refuses_a_v1_pool(tmp_path, capsys):
    pool = tmp_path / "pool.json"
    pool.write_text('{"candidates": []}', encoding="utf-8")
    code = cli.main(["draw-v2", "--pool", str(pool), "--pulse-time", PULSE_TIME,
                     "--out", str(tmp_path / "d.json")])
    assert code == 2
    assert "scheme" in capsys.readouterr().err
    assert not (tmp_path / "d.json").exists()


@pytest.mark.parametrize("when", ["2999-01-01T00:00Z", "2026-01-05T16:00"])
def test_draw_v2_refuses_future_or_naive_pulse_time(tmp_path, capsys, when):
    pool = tmp_path / "pool.json"
    pool.write_text('{"scheme": "xbrlrag-heldout-v2", "candidates": []}', encoding="utf-8")
    code = cli.main(["draw-v2", "--pool", str(pool), "--pulse-time", when,
                     "--out", str(tmp_path / "d.json")])
    assert code == 2
    assert "refusing" in capsys.readouterr().err
    assert not (tmp_path / "d.json").exists()


def test_draw_v2_writes_result_pulse_and_pool_hash(tmp_path, inputs, monkeypatch, capsys):
    assert cli.main(_pool_args(tmp_path, inputs)) == 0
    pool_bytes = (tmp_path / "pool.json").read_bytes()
    fake = _FakeFilings()
    monkeypatch.setattr(cli, "NistBeaconSource", _FakeBeacon)
    monkeypatch.setattr(cli, "EdgarSubmissionsSource", lambda *_a: fake)
    monkeypatch.setattr(cli, "resolve_sec_identity", lambda _identity: "x y@z")
    capsys.readouterr()

    code = cli.main(["draw-v2", "--pool", str(tmp_path / "pool.json"),
                     "--pulse-time", PULSE_TIME, "--out", str(tmp_path / "draw.json")])
    assert code == 0
    data = (tmp_path / "draw.json").read_bytes()
    draw = json.loads(data)
    assert draw["scheme"] == "xbrlrag-heldout-v2"
    assert draw["pulse"] == PULSE
    assert draw["pool_sha256"] == hashlib.sha256(pool_bytes).hexdigest()
    assert draw["output_value"] == PULSE["outputValue"].upper()
    # 2 candidates per stratum, all eligible: both drawn, shortfall 4 + 2 - 2 = 4
    for stratum, symbols in (("S", {"SWA", "SWB"}), ("D", {"DCA", "DCB"})):
        entry = draw["strata"][stratum]
        assert {e["symbol"] for e in entry["drawn"]} == symbols
        assert entry["alternates"] == []
        assert entry["shortfall"] == 4
    assert sorted(fake.calls) == ["0000000101", "0000000102", "0000000201", "0000000202"]
    assert f"{hashlib.sha256(data).hexdigest()}  draw.json" in capsys.readouterr().out


def test_draw_v2_seed_from_pool_uses_the_pool_sha512(tmp_path, inputs, monkeypatch, capsys):
    assert cli.main(_pool_args(tmp_path, inputs)) == 0
    pool_bytes = (tmp_path / "pool.json").read_bytes()

    class _NoBeacon:
        def pulse(self, timestamp_utc: str) -> dict:
            raise AssertionError("the pool seed needs no beacon")

    monkeypatch.setattr(cli, "NistBeaconSource", _NoBeacon)
    monkeypatch.setattr(cli, "EdgarSubmissionsSource", lambda *_a: _FakeFilings())
    monkeypatch.setattr(cli, "resolve_sec_identity", lambda _identity: "x y@z")

    code = cli.main(["draw-v2", "--pool", str(tmp_path / "pool.json"), "--seed-from-pool",
                     "--out", str(tmp_path / "draw.json")])
    assert code == 0
    draw = json.loads((tmp_path / "draw.json").read_bytes())
    # oracle: the seed is the SHA-512 of the pool file's bytes, computed here independently
    expected = hashlib.sha512(pool_bytes).hexdigest().upper()
    assert draw["output_value"] == expected
    assert draw["seed"] == {"source": "sha512 of the pool file's bytes", "value": expected}
    assert "pulse" not in draw
    # rerunning gives the same bytes: the draw is recomputable from the committed pool
    first = (tmp_path / "draw.json").read_bytes()
    assert cli.main(["draw-v2", "--pool", str(tmp_path / "pool.json"), "--seed-from-pool",
                     "--out", str(tmp_path / "draw2.json")]) == 0
    assert (tmp_path / "draw2.json").read_bytes() == first


def test_draw_v2_needs_exactly_one_seed(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["draw-v2", "--pool", str(tmp_path / "p.json"), "--out", str(tmp_path / "d")])
    with pytest.raises(SystemExit):
        cli.main(["draw-v2", "--pool", str(tmp_path / "p.json"), "--seed-from-pool",
                  "--pulse-time", PULSE_TIME, "--out", str(tmp_path / "d")])
