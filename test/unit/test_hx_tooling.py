"""Unit tests for H-X's CLI, Postgres store and template, beyond the locked spec interface.

The CLI runs against a fake ``HxKeyFactStore`` (the I/O seam) with synthetic candidates;
the Postgres store runs against a fake psycopg2 connection that records its queries.
Every question, ticker and amount is made up. Spec: docs/specs/hx-authoring.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from src.eval.set_b.core import load_authoring
from src.eval.set_b.hx import SUBTYPES, validate_hx
from src.eval.set_b.hx_base import HxKeyFactStore
from src.eval.set_b.hx_pg_store import (
    COLUMN_TOLERANCE_DAYS,
    PostgresHxKeyFactStore,
    filing_params,
)

REPO = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "eval" / "hx.py"
_spec = importlib.util.spec_from_file_location("hx_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

NS = "http://fasb.org/us-gaap/2024"
QUESTION = "Qvzk secret wording 4417?"
NOTE = "Wzrp private note 9902"
ACC = "0000000007-25-000011"


def _cand(fact_id, value, end="2025-06-30"):
    return {"fact_id": fact_id, "concept": f"{NS}:Revenues", "concept_family": None,
            "unit": "USD", "value": value, "period_start": "2024-07-01", "period_end": end,
            "dimensional": False, "member_label": None}


class _FakeStore(HxKeyFactStore):
    def __init__(self):
        self.calls = []

    def facts_for(self, ticker, fiscal_year):
        self.calls.append(("facts_for", ticker, fiscal_year))
        return {("DEVQA", 2025): [_cand(1, 300e6)], ("DEVQB", 2025): [_cand(2, 200e6)],
                ("DEVQB", 2024): [_cand(3, 150e6)]}.get((ticker, fiscal_year), [])

    def facts_in_filing(self, ticker, accession, fiscal_year):
        self.calls.append(("facts_in_filing", ticker, accession, fiscal_year))
        if (ticker, accession, fiscal_year) == ("DEVQB", ACC, 2024):
            return [_cand(4, 155e6, end="2024-06-30")]
        return []


def _yaml(top="300"):
    return (
        "cases:\n"
        "  - id: s1\n"
        f"    question: \"{QUESTION}\"\n"
        f"    note: \"{NOTE}\"\n"
        "    kind: cross_filer\n"
        "    subtype: screen_top\n"
        "    fy: 2025\n"
        "    unit: USD\n"
        "    universe: [DEVQA, DEVQB]\n"
        "    rows:\n"
        f"      - {{ticker: DEVQA, expected: \"{top}\", scale: millions}}\n"
        "      - {ticker: DEVQB, expected: \"200\", scale: millions}\n"
        "  - id: s2\n"
        f"    question: \"{QUESTION} B\"\n"
        "    kind: cross_period\n"
        "    subtype: point_in_time\n"
        "    ticker: DEVQB\n"
        "    fy: 2024\n"
        "    unit: USD\n"
        f"    as_originally_reported: {{expected: \"150\", scale: millions,"
        f" accession: \"0000000007-24-000003\"}}\n"
        f"    latest: {{expected: \"155\", scale: millions, accession: \"{ACC}\"}}\n"
        "  - id: s3\n"
        f"    question: \"{QUESTION} C\"\n"
        "    kind: text\n"
        "    ticker: DEVQA\n"
        "    fy: 2025\n"
        "    reference_answer: \"Xqjw reference 5531\"\n"
        "    key_points: [\"Kpqz point 1187\"]\n"
        f"    passages: [{{accession: \"{ACC}\", quote: \"Pzqx quote 7741\"}}]\n"
    )


def _write(tmp_path, text, name="hx.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _filers(tmp_path, dev=("DEVQA", "DEVQB"), held=("HOLDQ",)):
    return _write(tmp_path, json.dumps({"dev_filers": list(dev), "heldout_filers": list(held)}),
                  "filers.yaml")


def _assert_quiet(out):
    for bad in (QUESTION, "Qvzk", NOTE, "Wzrp", "Xqjw", "Kpqz", "Pzqx", "DEVQA", "DEVQB",
                "HOLDQ", ACC, "300000000", "155000000"):
        assert bad not in out


def test_validate_exit_codes_and_no_leak(tmp_path, capsys):
    filers = _filers(tmp_path)
    assert cli.main(["validate", str(_write(tmp_path, _yaml())), "--filers", str(filers)]) == 0
    printed = capsys.readouterr().out
    assert "errors: 0" in printed
    assert "cross_filer 1" in printed
    assert "2 distinct filers" in printed
    _assert_quiet(printed)

    held = _filers(tmp_path, dev=("DEVQA",), held=("DEVQB",))
    assert cli.main(["validate", str(_write(tmp_path, _yaml())), "--filers", str(held)]) == 1
    printed = capsys.readouterr().out
    assert "s1: universe[1]: names a held-out filer" in printed
    _assert_quiet(printed)


def test_check_keys_reads_filings_and_reports_without_leaks(tmp_path, capsys):
    store = _FakeStore()
    args = ["check-keys", str(_write(tmp_path, _yaml())), "--filers", str(_filers(tmp_path))]
    # The original 150 sits in no filing of the fake: s2 is not_found.
    assert cli.main(args, store=store) == 1
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    assert "not_found: 1" in printed
    assert "verified: 1" in printed
    assert "skipped: 1" in printed
    assert "    as_originally_reported: not_found, 0 classes" in printed
    assert Counter(store.calls)[("facts_in_filing", "DEVQB", ACC, 2024)] == 1
    assert ("facts_for", "DEVQB", 2024) not in store.calls


def test_build_seal_verify_round_trip(tmp_path, capsys):
    class _Store(_FakeStore):
        """The original 150 in its own filing, the recast 155 in the later one."""

        def facts_in_filing(self, ticker, accession, fiscal_year):
            self.calls.append(("facts_in_filing", ticker, accession, fiscal_year))
            if accession == ACC:
                return [_cand(4, 155e6, end="2024-06-30")]
            return [_cand(5, 150e6, end="2024-06-30")]

    out = tmp_path / "vault" / "heldout_x_v1.json"
    args = ["build", str(_write(tmp_path, _yaml())), "--filers", str(_filers(tmp_path)),
            "--out", str(out)]
    assert cli.main(args, store=_Store()) == 0
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    data = out.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert f"sha256 {digest}" in printed
    suite = json.loads(data)
    assert [c["id"] for c in suite["cases"]] == ["hx-s1", "hx-s2", "hx-s3"]
    assert suite["cases"][1]["fact_id_classes"] == [[5], [4]]
    assert NOTE.encode() not in data

    assert cli.main(["seal", str(out)]) == 0
    assert capsys.readouterr().out.strip() == f"{digest}  heldout_x_v1.json"
    assert cli.main(["verify", str(out), digest.upper()]) == 0
    capsys.readouterr()
    assert cli.main(["verify", str(out), "0" * 64]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_build_fails_on_an_unverified_case_unless_allowed(tmp_path, capsys):
    path = _write(tmp_path, _yaml(top="301"))
    filers = _filers(tmp_path)
    out = tmp_path / "x.json"
    assert cli.main(["build", str(path), "--filers", str(filers), "--out", str(out)],
                    store=_FakeStore()) == 1
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    assert "not built: unverified case(s): s1, s2" in printed
    assert not out.exists()
    assert cli.main(["build", str(path), "--filers", str(filers), "--out", str(out),
                     "--allow-unverified"], store=_FakeStore()) == 0
    suite = json.loads(out.read_bytes())
    assert suite["excluded"] == ["s1", "s2"]
    assert suite["n_cases"] == 1


def test_build_refuses_an_out_path_in_the_repo(tmp_path, capsys):
    out = REPO / "data" / "hx_should_not_exist.json"
    code = cli.main(["build", str(_write(tmp_path, _yaml())), "--filers",
                     str(_filers(tmp_path)), "--out", str(out)], store=_FakeStore())
    assert code == 1
    assert not out.exists()
    assert "inside the repo" in capsys.readouterr().out


def test_bad_inputs_exit_2_without_content(tmp_path, capsys):
    filers = _filers(tmp_path)
    broken = _write(tmp_path, f"cases:\n  - id: s1\n    question: \"{QUESTION}\n  : [\n")
    assert cli.main(["validate", str(broken), "--filers", str(filers)]) == 2
    printed = capsys.readouterr().out
    assert "not valid YAML (line" in printed
    _assert_quiet(printed)
    assert cli.main(["validate", str(_write(tmp_path, "other: 1\n")),
                     "--filers", str(filers)]) == 2
    assert "cases" in capsys.readouterr().out
    overlap = _filers(tmp_path, dev=("DEVQA", "HOLDQ"), held=("HOLDQ",))
    assert cli.main(["validate", str(_write(tmp_path, _yaml())), "--filers",
                     str(overlap)]) == 2
    printed = capsys.readouterr().out
    assert "1 ticker(s) are in both lists" in printed
    _assert_quiet(printed)


@pytest.mark.parametrize("text", ["[1, 2]", "dev_filers: [A]\n",
                                  "dev_filers: []\nheldout_filers: []\n",
                                  "dev_filers: [A, 3]\nheldout_filers: []\n", "a: [\n"])
def test_parse_filer_sets_rejects_bad_documents(text):
    with pytest.raises(cli.InputError):
        cli.parse_filer_sets(text)


def test_parse_filer_sets():
    dev, held = cli.parse_filer_sets("dev_filers: [A, ' B ']\nheldout_filers: [C]\n")
    assert dev == frozenset({"A", "B"})
    assert held == frozenset({"C"})


def test_template_covers_every_shape_and_fails_only_on_its_example_filers():
    cases = load_authoring((REPO / "configs" / "eval" / "hx_template.yaml").read_text(
        encoding="utf-8"))
    assert all(c["question"].startswith("EXAMPLE QUESTION:") for c in cases)
    got = validate_hx(cases, frozenset({"DEVQA"}), frozenset({"HOLDQ"}))
    assert got["mix"]["kind"] == {"cross_filer": 6, "cross_period": 3, "compound": 1,
                                  "honesty": 3, "text": 1}
    want = {f"{k}.{s}" for k, subs in SUBTYPES.items() for s in subs}
    assert set(got["mix"]["subtype"]) == want
    assert got["errors"]
    assert all("is not a development filer" in e for e in got["errors"])
    # With the example filers declared, every example is valid.
    examples = frozenset({"EXAMPLE", "EXAMPLEB", "EXAMPLEC", "EXAMPLED"})
    assert validate_hx(cases, examples, frozenset())["errors"] == []


def test_cli_imports_no_pipeline_llm_or_prompts():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{_SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "import src.eval.set_b.hx_pg_store\n"
        "bad = [n for n in sys.modules if n.split('.')[0] == 'prompts' or "
        "n.startswith(('src.retriever', 'src.pipelines', 'src.llm'))]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                         text=True, timeout=120, check=True)
    assert out.stdout.strip() == "[]"


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.queries.append((sql, params))

    def fetchall(self):
        return list(self.conn.rows)


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []
        self.cursor_factories = []
        self.closed = False

    def cursor(self, cursor_factory=None):
        self.cursor_factories.append(cursor_factory)
        return _Cursor(self)

    def close(self):
        self.closed = True


def _row(fact_id, end):
    return (fact_id, f"{NS}:Revenues", "Revenue", "USD", Decimal("155000000"),
            dt.date(2023, 7, 1), end, False, None, None, False, None)


def test_facts_in_filing_is_parameterised_cached_and_shifts_the_end_date(caplog):
    conn = _Conn([_row(42, dt.date(2024, 7, 1))])
    store = PostgresHxKeyFactStore(conn)
    with caplog.at_level("INFO"):
        got = store.facts_in_filing("DEVQB", ACC, 2024)
        again = store.facts_in_filing("DEVQB", ACC, 2024)
    assert got == again
    assert got[0]["fact_id"] == 42
    assert got[0]["period_end"] == "2024-06-30"  # stored one day late (Arelle convention)
    assert len(conn.queries) == 1
    sql, params = conn.queries[0]
    assert params == {"ticker": "DEVQB", "accession": ACC, "fiscal_year": 2024,
                      "tolerance": COLUMN_TOLERANCE_DAYS}
    assert params == filing_params("DEVQB", ACC, 2024)
    for name in params:
        assert f"%({name})s" in sql
    assert "DEVQB" not in sql and ACC not in sql
    assert "is_canonical_filing" not in sql  # a superseded filing is read too (S1)
    assert "DEVQB" not in caplog.text and ACC not in caplog.text
    got[0]["value"] = -1.0
    assert store.facts_in_filing("DEVQB", ACC, 2024)[0]["value"] == 155e6  # copies


def _filer_row(fact_id, accession, fy, filed, start, stored_end, value):
    """A flat ``mv_financial_facts`` row of an FY 10-K whose DEI period end is the day
    before ``stored_end`` (the store's next-midnight end)."""
    return {
        "fact_id": fact_id, "accession_no": accession, "fiscal_year": fy,
        "fiscal_period": "FY", "filed_date": filed, "is_amendment": False,
        "is_canonical_filing": True,
        "document_period_end": stored_end - dt.timedelta(days=1),
        "concept": f"{NS}:Revenues", "concept_family": "Revenue", "unit": "USD",
        "value": Decimal(value), "period_type": "duration", "period_start": start,
        "period_end": stored_end, "duration_kind": "annual", "is_current_period": True,
        "segment_axis": None, "segment_member": None, "segment_label": None,
        "has_multiple_dimensions": False, "dimension_labels": None,
    }


def test_facts_for_runs_set_b_query_without_logging_the_filer(caplog):
    own = _filer_row(7, ACC, 2025, dt.date(2025, 8, 20), dt.date(2024, 7, 1),
                     dt.date(2025, 7, 1), "155000000")
    # The FY2026 10-K reports the FY2025 period again (restated): S1 cites it instead.
    later = dict(_filer_row(9, "0000000002-26-000001", 2026, dt.date(2026, 8, 20),
                            dt.date(2025, 7, 1), dt.date(2026, 7, 1), "170000000"))
    restated = dict(later, fact_id=8, period_start=dt.date(2024, 7, 1),
                    period_end=dt.date(2025, 7, 1), is_current_period=False,
                    value=Decimal("150000000"))
    conn = _Conn([own, later, restated])
    store = PostgresHxKeyFactStore(conn)
    with caplog.at_level("INFO"):
        got = store.facts_for("DEVQA", 2025)
        store.facts_for("DEVQA", 2025)
    assert [(c["fact_id"], c["value"], c["period_end"]) for c in got] == [
        (8, 150e6, "2025-06-30")]
    assert len(conn.queries) == 1
    sql, params = conn.queries[0]
    assert params == {"ticker": "DEVQA", "fiscal_year": 2025}
    for name in params:
        assert f"%({name})s" in sql
    assert "DEVQA" not in sql
    assert conn.cursor_factories[0] is not None  # a dict cursor
    assert "DEVQA" not in caplog.text
    store.close()
    assert not conn.closed  # a connection passed in is the caller's to close
