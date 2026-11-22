"""Unit tests for set B's CLI and Postgres store, beyond the locked spec interface.

The CLI runs against a fake ``KeyFactStore`` (the I/O seam) with synthetic candidates;
the store's row mapping and label cleaning are pure. One read-only test reads the real
``xbrl_db`` and skips when it is unreachable. Spec: docs/specs/set-b-tooling.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from src.eval.set_b.base import KeyFactStore
from src.eval.set_b.core import find_classes
from src.eval.set_b.pg_store import (
    PostgresKeyFactStore,
    candidate_from_row,
    clean_member_label,
    member_label,
)

REPO = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "eval" / "set_b.py"
_spec = importlib.util.spec_from_file_location("set_b_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

SUITE = REPO / "test" / "eval" / "suites" / "sql_derived_v1.json"
NS = "http://fasb.org/us-gaap/2024"
QUESTION = "Hqzv secret wording 6621?"
NOTE = "Jxwk private note 7730"


def _cand(fact_id, local, value, member=None):
    return {"fact_id": fact_id, "concept": f"{NS}:{local}", "concept_family": None,
            "unit": "USD", "value": value, "period_start": "2024-07-01",
            "period_end": "2025-06-30", "dimensional": member is not None,
            "member_label": member}


class _FakeStore(KeyFactStore):
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def facts_for(self, ticker, fiscal_year):
        self.calls.append((ticker, fiscal_year))
        return [dict(c) for c in self.facts.get((ticker, fiscal_year), [])]


def _store():
    return _FakeStore({("MSFT", 2025): [
        _cand(11, "Revenues", 245_122e6),
        _cand(31, "Revenues", 10e6, member="Alpha Segment"),
        _cand(32, "Revenues", 20e6, member="Beta Segment"),
    ]})


def _yaml(expected="245,122"):
    return (
        "cases:\n"
        "  - id: q1\n"
        f"    question: \"{QUESTION}\"\n"
        f"    note: \"{NOTE}\"\n"
        "    kind: lookup\n"
        "    ticker: MSFT\n"
        "    fy: 2025\n"
        f"    expected: \"{expected}\"\n"
        "    scale: millions\n"
        "    unit: USD\n"
        "  - id: q2\n"
        f"    question: \"{QUESTION} B\"\n"
        "    kind: breakdown\n"
        "    ticker: MSFT\n"
        "    fy: 2025\n"
        "    unit: USD\n"
        "    rows:\n"
        "      - {member: alpha, expected: \"10\", scale: millions}\n"
        "      - {member: beta, expected: \"20\", scale: millions}\n"
    )


def _write(tmp_path, text):
    path = tmp_path / "set_b.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _assert_quiet(out):
    for bad in (QUESTION, "Hqzv", NOTE, "Jxwk", "245,122", "245122"):
        assert bad not in out


def test_is_inside(tmp_path):
    assert cli.is_inside(REPO / "data" / "x.json", REPO)
    assert cli.is_inside(REPO, REPO)
    assert not cli.is_inside(tmp_path / "x.json", REPO)


def test_build_refuses_an_out_path_in_the_repo(tmp_path, capsys):
    out = REPO / "data" / "set_b_should_not_exist.json"
    code = cli.main(["build", str(_write(tmp_path, _yaml())), "--out", str(out)],
                    store=_store())
    assert code == 1
    assert not out.exists()
    assert "inside the repo" in capsys.readouterr().out


def test_build_seal_verify_round_trip(tmp_path, capsys):
    out = tmp_path / "vault" / "heldout_b_v1.json"
    store = _store()
    assert cli.main(["build", str(_write(tmp_path, _yaml())), "--out", str(out)],
                    store=store) == 0
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    data = out.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert f"sha256 {digest}" in printed
    suite = json.loads(data)
    assert [c["id"] for c in suite["cases"]] == ["b-q1", "b-q2"]
    assert suite["cases"][1]["expected_rows"] == [{"entity": "Alpha Segment", "value": 10e6},
                                                  {"entity": "Beta Segment", "value": 20e6}]
    assert NOTE.encode() not in data
    assert store.calls == [("MSFT", 2025), ("MSFT", 2025)]

    assert cli.main(["seal", str(out)]) == 0
    assert capsys.readouterr().out.strip() == f"{digest}  heldout_b_v1.json"
    assert cli.main(["verify", str(out), digest.upper()]) == 0
    capsys.readouterr()
    assert cli.main(["verify", str(out), "0" * 64]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_build_fails_on_an_unverified_case_unless_allowed(tmp_path, capsys):
    path = _write(tmp_path, _yaml(expected="245,999"))
    out = tmp_path / "b.json"
    assert cli.main(["build", str(path), "--out", str(out)], store=_store()) == 1
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    assert "q1" in printed
    assert not out.exists()
    assert cli.main(["build", str(path), "--out", str(out), "--allow-unverified"],
                    store=_store()) == 0
    suite = json.loads(out.read_bytes())
    assert suite["excluded"] == ["q1"]
    assert suite["n_cases"] == 1


def test_check_keys_exit_codes_and_no_leak(tmp_path, capsys):
    assert cli.main(["check-keys", str(_write(tmp_path, _yaml()))], store=_store()) == 0
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    assert "verified: 2" in printed
    assert cli.main(["check-keys", str(_write(tmp_path, _yaml("245,999")))],
                    store=_store()) == 1
    printed = capsys.readouterr().out
    _assert_quiet(printed)
    assert "not_found: 1" in printed


def test_validate_exit_codes(tmp_path, capsys):
    assert cli.main(["validate", str(_write(tmp_path, _yaml()))]) == 0
    printed = capsys.readouterr().out
    assert "errors: 0" in printed
    _assert_quiet(printed)
    bad = _yaml().replace("ticker: MSFT", "ticker: EXAMPLE", 1)
    assert cli.main(["validate", str(_write(tmp_path, bad))]) == 1
    printed = capsys.readouterr().out
    assert "q1: ticker" in printed
    _assert_quiet(printed)


def test_template_parses_and_fails_validation_on_purpose():
    from src.eval.set_b.core import load_authoring, validate

    text = (REPO / "configs" / "eval" / "set_b_template.yaml").read_text(encoding="utf-8")
    cases = load_authoring(text)
    assert sorted({c["kind"] for c in cases}) == ["breakdown", "compute", "dimensional",
                                                  "lookup"]
    errors = validate(cases)["errors"]
    # Every example fails only on its EXAMPLE ticker.
    assert len(errors) == len(cases)
    assert all(": ticker: " in e for e in errors)


def test_label_recording_store():
    rec = cli.LabelRecordingStore(_store())
    rec.facts_for("MSFT", 2025)
    assert rec.labels == {31: "Alpha Segment", 32: "Beta Segment"}


class _SplitStore(_FakeStore):
    """A fake whose breakdown read differs from its lone-member read."""

    def __init__(self, facts, split):
        super().__init__(facts)
        self.split = split
        self.breakdown_calls = []

    def breakdown_facts_for(self, ticker, fiscal_year):
        self.breakdown_calls.append((ticker, fiscal_year))
        return [dict(c) for c in self.split.get((ticker, fiscal_year), [])]


def test_label_recording_store_wraps_the_breakdown_read():
    split = {("MSFT", 2025): [_cand(41, "Revenues", 7e6, member="Gamma Segment")]}
    inner = _SplitStore(_store().facts, split)
    rec = cli.LabelRecordingStore(inner)
    got = rec.breakdown_facts_for("MSFT", 2025)
    assert [c["fact_id"] for c in got] == [41]
    assert inner.breakdown_calls == [("MSFT", 2025)]
    assert inner.calls == []  # never the inner store's facts_for
    assert rec.labels == {41: "Gamma Segment"}
    # A store without a breakdown rule falls back to facts_for (the seam's default).
    plain = cli.LabelRecordingStore(_store())
    assert [c["fact_id"] for c in plain.breakdown_facts_for("MSFT", 2025)] == [11, 31, 32]
    assert plain.labels == {31: "Alpha Segment", 32: "Beta Segment"}


def test_cli_imports_no_pipeline_llm_or_prompts():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{_SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "import src.eval.set_b.pg_store\n"
        "bad = [n for n in sys.modules if n.split('.')[0] == 'prompts' or "
        "n.startswith(('src.retriever', 'src.pipelines', 'src.llm'))]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                         text=True, timeout=120, check=True)
    assert out.stdout.strip() == "[]"


def test_clean_member_label_and_member_label():
    assert clean_member_label("Data Center [Member]") == "Data Center"
    assert clean_member_label("Commercial Paper [Member] and Level 2") == (
        "Commercial Paper and Level 2")
    assert member_label("iPhone [Member]", "aapl:IPhoneMember") == "iPhone"
    assert member_label(None, "http://www.apple.com/20240928:IPhoneMember") == "IPhoneMember"
    assert member_label(None, None) is None
    multi = {"srt:ProductOrServiceAxis": "iPhone [Member]",
             "srt:StatementGeographicalAxis": "Americas [Member]"}
    assert member_label("iPhone [Member]", "aapl:IPhoneMember", multi) == "iPhone | Americas"


def test_candidate_from_row_moves_the_end_back_one_day():
    row = (13550, f"{NS}:RevenueFromContractWithCustomerExcludingAssessedTax", "Revenue",
           "USD", Decimal("201183000000"), dt.date(2023, 10, 1), dt.date(2024, 9, 29), True,
           "iPhone [Member]", "aapl:IPhoneMember", False, None)
    assert candidate_from_row(row) == {
        "fact_id": 13550,
        "concept": f"{NS}:RevenueFromContractWithCustomerExcludingAssessedTax",
        "concept_family": "Revenue", "unit": "USD", "value": 201183000000.0,
        "period_start": "2023-10-01", "period_end": "2024-09-28", "dimensional": True,
        "member_label": "iPhone"}
    flat = (1, f"{NS}:Assets", None, "USD", Decimal("5"), None, "2024-09-29", False, None,
            None, False, None)
    got = candidate_from_row(flat)
    assert got["period_start"] is None
    assert got["period_end"] == "2024-09-28"
    assert got["member_label"] is None


def _eps_row(fact_id, unit, value):
    # The fact store writes a per-share unit as its measures joined by "*" ("shares*USD").
    return (fact_id, f"{NS}:EarningsPerShareDiluted", "EarningsPerShareDiluted", unit,
            Decimal(value), dt.date(2023, 10, 1), dt.date(2024, 9, 29), False, None, None,
            False, None)


@pytest.mark.parametrize("stored", ["shares*USD", "USD*shares"])
def test_per_share_key_resolves_against_the_stored_unit_form(stored):
    cands = [candidate_from_row(_eps_row(7, stored, "6.08"))]
    assert cands[0]["unit"] == "USD/shares"
    assert find_classes(cands, (6.08, 0.005), "USD/shares", False) == [[7]]


def test_per_share_key_does_not_match_another_currency():
    cands = [candidate_from_row(_eps_row(8, "shares*TWD", "6.08"))]
    assert cands[0]["unit"] == "TWD/shares"
    assert find_classes(cands, (6.08, 0.005), "USD/shares", False) == []


def test_per_share_key_does_not_match_plain_shares_or_money():
    cands = [candidate_from_row(_eps_row(9, "shares", "6.08")),
             candidate_from_row(_eps_row(10, "USD", "6.08"))]
    assert [c["unit"] for c in cands] == ["shares", "USD"]
    assert find_classes(cands, (6.08, 0.005), "USD/shares", False) == []
    assert find_classes(cands, (6.08, 0.005), "shares", False) == [[9]]


def _db_reachable() -> bool:
    try:
        store = PostgresKeyFactStore()
        try:
            store.facts_for("AAPL", 2024)
        finally:
            store.close()
        return True
    except Exception:  # isolation point: any connection failure means skip
        return False


_DB_REACHABLE = _db_reachable()


@pytest.mark.skipif(not _DB_REACHABLE, reason="xbrl_db not reachable")
def test_store_resolves_main_suite_facts_and_labels():
    # Read-only: a main-suite lookup's fact sits in the expected class. Dimensional and
    # breakdown citations are checked case by case in the test below.
    cases = json.loads(SUITE.read_text(encoding="utf-8"))["cases"]
    lookup = next(c for c in cases if c["kind"] == "lookup")
    with PostgresKeyFactStore() as store:
        cands = store.facts_for(lookup["ticker"], lookup["fy"])
        classes = find_classes(cands, (lookup["expected_value"], 0.0), lookup["unit"], False,
                               lookup.get("concept"))
        assert sorted(lookup["fact_id_classes"][0]) in classes


# Citations the store cannot hold, by name (docs/specs/set-b-dimensional-s1.md): GOOGL's
# reconciling lines are undimensioned facts of another concept (D4), and AMZN fact 7093 is a
# quarterly multi-axis cell (D5). Keyed by (case id, cited fact id).
_EXCLUDED_CITATIONS = {
    ("aq-3b-business-segment-breakdown:fy=2024-ticker=GOOGL", 34690),
    ("aq-3b-business-segment-breakdown:fy=2025-ticker=GOOGL", 34691),
    ("aq-4a-geographic-breakdown:fy=2024-ticker=GOOGL", 34690),
    ("aq-4a-geographic-breakdown:fy=2025-ticker=GOOGL", 34691),
    ("aq-8b-multidim-lookup:combo=DepreciationDepletionAndAmortization_Early_Ret-5a119941",
     7093),
}
# The v1 suite cites dimensional-s1's rows (S1) only from its rebuild on this date; an older
# committed suite cites the own filings' rows, which the store no longer answers when a later
# filing restates them.
_SUITE_REBUILT_ON = "2026-11-12"


def _suite_is_s1() -> bool:
    generated = json.loads(SUITE.read_text(encoding="utf-8")).get("generated_at") or ""
    return str(generated)[:10] >= _SUITE_REBUILT_ON


@pytest.mark.skipif(not _DB_REACHABLE, reason="xbrl_db not reachable")
@pytest.mark.skipif(not _suite_is_s1(),
                    reason="the committed v1 suite predates its dimensional-s1 rebuild; "
                           "runs once the rebuilt suite lands")
def test_store_resolves_every_dimensional_and_breakdown_citation():
    # Read-only (docs/specs/set-b-dimensional-s1.md, Notes): every dimensional case's class has
    # a member among facts_for's candidates, every breakdown row's class one among
    # breakdown_facts_for's with its expected entity as label; the named exclusions miss.
    cases = json.loads(SUITE.read_text(encoding="utf-8"))["cases"]
    missed: set[tuple[str, int]] = set()
    wrong_labels = []
    n_dim = n_rows = 0
    with PostgresKeyFactStore() as store:
        for case in cases:
            if case["kind"] == "dimensional":
                n_dim += 1
                ids = {c["fact_id"] for c in store.facts_for(case["ticker"], case["fy"])}
                if not ids & set(case["fact_id_classes"][0]):
                    missed.add((case["id"], case["expected_fact_ids"][0]))
            elif case["kind"] == "breakdown":
                by_id = {c["fact_id"]: c
                         for c in store.breakdown_facts_for(case["ticker"], case["fy"])}
                for cls, fact_id, row in zip(case["fact_id_classes"], case["expected_fact_ids"],
                                             case["expected_rows"], strict=True):
                    n_rows += 1
                    hits = [i for i in cls if i in by_id]
                    if not hits:
                        missed.add((case["id"], fact_id))
                        continue
                    label = by_id[fact_id if fact_id in by_id else hits[0]]["member_label"]
                    if label != row["entity"]:
                        wrong_labels.append((case["id"], fact_id, label, row["entity"]))
    assert n_dim and n_rows
    # Exactly the named exclusions the suite still cites: a rebuild's seeded draw can drop an
    # excluded case (the 11-12 post-G-e rebuild dropped the AMZN 7093 one), never add a miss.
    cited = {(c["id"], f) for c in cases for f in c.get("expected_fact_ids") or []}
    assert missed == _EXCLUDED_CITATIONS & cited
    assert wrong_labels == []
