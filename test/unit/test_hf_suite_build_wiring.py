"""The held-out wiring around the hf-suite-build library (docs/specs/hf-suite-build.md).

``configure_logging(log_dir=...)`` (requirement 11), the held-out mode of
``scripts/eval/build_v2_suite.py`` (12), ``scripts/eval/build_heldout_overlay.py`` (3) and
``scripts/eval/cut_fsds.py`` (10). The database, SEC and witness seams are faked; every
filer is made up (DEVA plays a development filer, DEVB a held-out one).
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.suites.intents import load_lexicon
from src.eval.suites.v2_suite import FactStoreSource
from src.logging_config import configure_logging, current_log_file

REPO = Path(__file__).resolve().parents[2]
M = 1_000_000
CIK = {"DEVA": "0000800001", "DEVB": "0000800002"}

BASE_LEXICON = """\
companies:
  DEVA: {short: Deva, legal: Deva Holdings Inc.}
metrics:
  Revenue: {label: revenue, synonyms: [net sales]}
universes:
  covered: [the covered companies]
period_ends:
  DEVA: {2023: "2023-12-31", 2024: "2024-12-31"}
"""
OVERLAY = """\
companies:
  DEVB: {short: Devb, legal: Devb Corporation}
period_ends:
  DEVB: {2023: "2023-12-31", 2024: "2024-12-31"}
"""


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_cli",
                                                  REPO / "scripts" / "eval" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_logging(monkeypatch):
    """No ``XBRLRAG_LOG_DIR``; the project's own logging restored afterwards."""
    monkeypatch.delenv("XBRLRAG_LOG_DIR", raising=False)
    yield
    monkeypatch.delenv("XBRLRAG_LOG_DIR", raising=False)
    configure_logging()


# --------------------------------------------------------------------------- logging


def test_configure_logging_moves_the_file_into_the_log_dir(tmp_path, clean_logging,
                                                           monkeypatch):
    configure_logging(log_dir=tmp_path / "a")
    assert current_log_file() is not None and current_log_file().parent == tmp_path / "a"
    monkeypatch.setenv("XBRLRAG_LOG_DIR", str(tmp_path / "b"))
    configure_logging()
    assert current_log_file().parent == tmp_path / "b"
    configure_logging(log_dir=tmp_path / "c")  # the argument wins over the variable
    assert current_log_file().parent == tmp_path / "c"
    with pytest.raises(ValueError):
        configure_logging(log_dir="relative/logs")


def test_configure_logging_without_override_keeps_the_repo_logs(clean_logging):
    configure_logging()
    assert current_log_file().parent == REPO / "logs"


# --------------------------------------------------------------------------- build_v2_suite


def _cf(ticker: str, revenue: tuple[int, int]) -> dict:
    accn = {2023: f"{CIK[ticker]}-24-000001", 2024: f"{CIK[ticker]}-25-000001"}
    filed = {2023: "2024-02-12", 2024: "2025-02-10"}

    def entry(year: int, val: int, report: int) -> dict:
        return {"start": f"{year}-01-01", "end": f"{year}-12-31", "val": val,
                "accn": accn[report], "fy": report, "fp": "FY", "form": "10-K",
                "filed": filed[report]}

    rows = [entry(2023, revenue[0] * M, 2023), entry(2024, revenue[1] * M, 2024),
            entry(2023, revenue[0] * M, 2024)]
    return {"cik": int(CIK[ticker]), "entityName": ticker,
            "facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}


DOCS = {CIK["DEVA"]: _cf("DEVA", (450, 500)), CIK["DEVB"]: _cf("DEVB", (420, 400))}
INVENTORY = {t: {"cik": CIK[t], "years": {y: {"Revenue": {"concept": "us-gaap:Revenues",
                                                          "unit": "USD"}}
                                          for y in (2023, 2024)}}
             for t in ("DEVA", "DEVB")}


class _Sec(SecSource):
    def company_facts(self, cik: str) -> dict:
        return copy.deepcopy(DOCS[str(cik).zfill(10)])

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are never read")


class _NoStore(FactStoreSource):
    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


class _Conn:
    def close(self) -> None:
        pass


def _heldout_args(tmp_path: Path, **over: str) -> list[str]:
    (tmp_path / "base.yaml").write_text(BASE_LEXICON, encoding="utf-8")
    (tmp_path / "overlay.yaml").write_text(OVERLAY, encoding="utf-8")
    (tmp_path / "tags.yaml").write_text("x: 1\n", encoding="utf-8")
    opts = {"--lexicon": str(tmp_path / "base.yaml"),
            "--lexicon-overlay": str(tmp_path / "overlay.yaml"),
            "--heldout-tags": str(tmp_path / "tags.yaml"),
            "--out-dir": str(tmp_path / "out"), "--log-dir": str(tmp_path / "logs"),
            "--share": "0", **over}
    args = ["--split", "heldout_filers", "--total", "24", "--per-intent", "1",
            "--salt", "s", "--sec-cache-only"]
    for flag, value in opts.items():
        if value:
            args += [flag, value]
    return args


@pytest.fixture
def cli(monkeypatch, clean_logging):
    """The build script with its database, SEC and witness seams faked."""
    module = _load("build_v2_suite")
    calls: list[str] = []

    def connect():
        calls.append("connect")
        return _Conn()

    monkeypatch.setattr(module, "connect_readonly", connect)
    monkeypatch.setattr(module, "read_corpus_inventory", lambda conn: copy.deepcopy(INVENTORY))
    monkeypatch.setattr(module, "read_candidate_concepts", lambda conn: {})
    monkeypatch.setattr(module, "fsds_quarters", lambda root: [])
    monkeypatch.setattr(module, "KeyWitness", SimpleNamespace(from_paths=lambda *a, **k: None))
    monkeypatch.setattr(module, "_sec_source", lambda cache_only, identity: _Sec())
    monkeypatch.setattr(module, "PostgresFactStoreSource", lambda conn: _NoStore())
    monkeypatch.setattr(module, "load_sector_tags", lambda path: {"filers": {}})
    module.calls = calls
    return module


@pytest.mark.parametrize("over", [
    {"--share": "0.15"},
    {"--lexicon-overlay": ""},
    {"--heldout-tags": ""},
    {"--out-dir": ""},
    {"--out-dir": str(REPO / "data" / "eval" / "hf")},
    {"--lexicon-overlay": str(REPO / "configs" / "eval" / "v2" / "lexicon.yaml")},
    {"--log-dir": str(REPO / "logs" / "heldout")},
])
def test_heldout_mode_refuses_before_any_database_read(cli, tmp_path, capsys, over):
    assert cli.main(_heldout_args(tmp_path, **over)) == 2
    assert cli.calls == []
    assert not (tmp_path / "out").exists()
    assert "refused" in capsys.readouterr().err


def test_heldout_mode_refuses_a_log_file_inside_the_repository(cli, tmp_path):
    args = argparse.Namespace(lexicon_overlay=tmp_path / "o.yaml",
                              heldout_tags=tmp_path / "t.yaml", out_dir=tmp_path / "out",
                              share=0.0)
    assert cli.heldout_preflight(args, REPO, tmp_path / "x.log") == []
    assert len(cli.heldout_preflight(args, REPO, REPO / "logs" / "x.log")) == 1
    assert len(cli.heldout_preflight(args, REPO, None)) == 1


def test_heldout_mode_builds_heldout_files_and_prints_counts_only(cli, tmp_path, capsys):
    assert cli.main(_heldout_args(tmp_path)) == 0
    out = capsys.readouterr()
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["heldout_hf.json", "heldout_hf_intents.json", "heldout_hf_manifest.json"]
    suite = json.loads((tmp_path / "out" / "heldout_hf.json").read_text(encoding="utf-8"))
    assert suite["name"] == "heldout_hf" and suite["n_cases"] > 0
    assert {c["tags"][0] for c in suite["cases"]} == {"split:heldout_filers"}
    # every kept intent involves DEVB: its lookups, or a cross-filer shape over covered
    assert all("DEVB" in o["tickers"] or o["universe"] == "covered"
               for o in json.loads((tmp_path / "out" / "heldout_hf_intents.json")
                                   .read_text(encoding="utf-8")))
    text = out.out + out.err
    for secret in ("DEVA", "DEVB", "Deva", "Devb", *CIK.values(), "800002"):
        assert secret not in text
    assert suite["content_hash"] in out.out
    assert current_log_file().parent == tmp_path / "logs"


def test_heldout_mode_reports_an_error_by_type_only(cli, tmp_path, capsys, monkeypatch):
    def boom(conn):
        raise ValueError("DEVB revenue 400000000")

    monkeypatch.setattr(cli, "read_corpus_inventory", boom)
    assert cli.main(_heldout_args(tmp_path)) == 1
    out = capsys.readouterr()
    assert "ValueError" in out.err
    assert "DEVB" not in out.out + out.err and "400000000" not in out.out + out.err
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = current_log_file().read_text(encoding="utf-8")
    assert "Traceback" in log and "DEVB" in log  # the traceback stays in the held-out tree
    assert not (tmp_path / "out").exists()


def test_heldout_groups_wiring_reads_the_committed_definitions(tmp_path):
    cli = _load("build_v2_suite")
    (tmp_path / "base.yaml").write_text(
        (REPO / "configs" / "eval" / "v2" / "lexicon.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    (tmp_path / "overlay.yaml").write_text(
        "companies:\n  ZZQR: {short: Zzqr, legal: Zzqr Stores Inc.}\n", encoding="utf-8")
    (tmp_path / "tags.yaml").write_text(
        'as_of: "2026-11-10"\nsource: synthetic\nfilers:\n'
        '  - {cik: "0000999991", ticker: ZZQR, gics_sector: Consumer Staples, '
        "industry: Retail}\n", encoding="utf-8")
    args = argparse.Namespace(lexicon=tmp_path / "base.yaml",
                              lexicon_overlay=tmp_path / "overlay.yaml",
                              heldout_tags=tmp_path / "tags.yaml",
                              groups=REPO / "configs" / "eval" / "v2" / "groups.yaml")
    lexicon, heldout, groups = cli.heldout_lexicon_and_groups(args)
    assert heldout == ["ZZQR"]
    assert "ZZQR" in lexicon.companies
    committed = cli.load_universe_groups(args.groups)["groups"]
    assert groups["retailers"] == sorted([*committed["retailers"], "ZZQR"])
    assert groups["semiconductors"] == sorted(committed["semiconductors"])


# --------------------------------------------------------------------------- overlay script


class _Cursor:
    def __init__(self, rows: dict[str, list[tuple]]) -> None:
        self.rows, self.last = rows, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql: str) -> None:
        self.last = sql

    def fetchall(self) -> list[tuple]:
        return self.rows["entity" if "entity_master" in self.last else "filing"]


class _OverlayConn:
    def __init__(self, rows) -> None:
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)

    def close(self) -> None:
        pass


def _draw(tmp_path: Path) -> Path:
    entry = {"cik": "0000800002", "symbol": "DEVB", "name": "Devb", "key": "ab", "rank": 1,
             "sector": "Industrials"}
    path = tmp_path / "draw.json"
    path.write_text(json.dumps({"scheme": "xbrlrag-heldout-v2", "strata": {
        "S": {"drawn": [entry], "alternates": []}, "D": {"drawn": [], "alternates": []}}}),
        encoding="utf-8")
    return path


def _overlay_rows(deva_2024_end: str) -> dict[str, list[tuple]]:
    return {
        "entity": [("0000800001", "Deva Holdings Inc.", "DEVA"),
                   ("0000800002", "Devb Corporation", "DEVB")],
        "filing": [("0000800001", "0000800001-24-000001", "10-K", 2023, "2024-02-12",
                    "2023-12-31"),
                   ("0000800001", "0000800001-25-000001", "10-K", 2024, "2025-02-10",
                    deva_2024_end),
                   ("0000800002", "0000800002-25-000001", "10-K", 2024, "2025-02-11",
                    "2024-12-28"),
                   # a year beyond the committed lexicon: counted, not a mismatch
                   ("0000800001", "0000800001-26-000001", "10-K", 2025, "2026-02-10",
                    "2025-12-31")],
    }


def test_overlay_script_writes_counts_only_and_self_checks(tmp_path, capsys, monkeypatch):
    cli = _load("build_heldout_overlay")
    (tmp_path / "base.yaml").write_text(BASE_LEXICON, encoding="utf-8")
    monkeypatch.setattr(cli, "connect_readonly",
                        lambda: _OverlayConn(_overlay_rows("2024-12-31")))
    out = tmp_path / "ho" / "overlay.yaml"
    out.parent.mkdir()
    argv = ["--draw", str(_draw(tmp_path)), "--out", str(out), "--lexicon",
            str(tmp_path / "base.yaml")]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert "filers written 1" in text and "mismatches 0" in text
    assert "years beyond the committed lexicon 1" in text
    for secret in ("DEVA", "DEVB", "Devb", "2024-12-28", "800002"):
        assert secret not in text
    merged = load_lexicon(out)
    assert merged.companies == {"DEVB": {"short": "Devb", "legal": "Devb Corporation"}}
    assert str(merged.period_ends["DEVB"][2024]) == "2024-12-28"

    # a development filer's period end that differs from the committed one: nothing written
    out.unlink()
    monkeypatch.setattr(cli, "connect_readonly",
                        lambda: _OverlayConn(_overlay_rows("2024-12-29")))
    assert cli.main(argv) == 1
    assert "mismatches 1" in capsys.readouterr().out
    assert not out.exists()

    assert cli.main(["--draw", str(_draw(tmp_path)), "--out",
                     str(REPO / "data" / "overlay.yaml")]) == 2


# --------------------------------------------------------------------------- cut_fsds script


def test_cut_fsds_script(tmp_path, capsys):
    cli = _load("cut_fsds")
    adsh = "0000900001-24-000001"
    tables = {"sub": f"adsh\tcik\tform\n{adsh}\t900001\t10-K\nX-1\t900002\t10-K\n",
              "num": f"adsh\ttag\tversion\tvalue\n{adsh}\tRevenues\tus-gaap/2023\t5\n",
              "pre": f"adsh\ttag\tversion\n{adsh}\tRevenues\tus-gaap/2023\n",
              "tag": "tag\tversion\nRevenues\tus-gaap/2023\nOther\tus-gaap/2023\n"}
    raw = tmp_path / "raw"
    raw.mkdir()
    with zipfile.ZipFile(raw / "2024q1.zip", "w") as z:
        for name, text in tables.items():
            z.writestr(f"{name}.txt", text)
    ciks = tmp_path / "ciks.txt"
    ciks.write_text("# filers\n0000900001\n\n", encoding="utf-8")
    assert cli.read_ciks(ciks) == ["0000900001"]
    out = tmp_path / "fsds"
    assert cli.main(["--raw-dir", str(raw), "--ciks-file", str(ciks), "--out-dir",
                     str(out)]) == 0
    assert "2024q1: sub 1, num 1, pre 1, tag 1" in capsys.readouterr().out
    assert (out / "2024q1" / "sub.tsv").read_text(encoding="utf-8") == \
        f"adsh\tcik\tform\n{adsh}\t900001\t10-K\n"
    assert cli.main(["--raw-dir", str(raw), "--cik", "900001", "--out-dir",
                     str(REPO / "data" / "fsds_cut")]) == 2
    bad = tmp_path / "bad.txt"
    bad.write_text("ABC\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cli.read_ciks(bad)
