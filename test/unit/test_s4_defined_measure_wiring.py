"""Wiring of the S4 defined measures into the lookup, the screen and the evidence
(docs/specs/s4-lookup-deferred-revenue-debt.md, Wiring). The database is faked at its
boundary (the three reads); the measure itself is the real ``defined_measure``.
Values are a hand-built HD-shaped filing: 300 + 4,600 + 48,500 = 53,400 (M)."""

from __future__ import annotations

import datetime
from contextlib import contextmanager

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.retriever.xbrl.citation_integrity import unmatched_numbers, unresolved_citation_ids
from src.retriever.xbrl.defined_measure_rows import (
    concept_name,
    defined_rules,
    measure_lines,
    read_filing_rows,
)
from src.retriever.xbrl.plan_executor import StepResult, StepStatus, defined_fact_ids
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
    XbrlFactstoreLookup,
)
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

M = 1e6
G = "http://fasb.org/us-gaap/2025:"
X = "http://www.example.com/20251231:"
ACC = "0000000001-26-000001"
CIK = "0000000001"
PE = datetime.date(2026, 1, 1)
BS = "0000003 - Statement - CONSOLIDATED BALANCE SHEETS"
BS_COPY = "9952151 - Statement - CONSOLIDATED BALANCE SHEETS"
IS = "0000002 - Statement - CONSOLIDATED STATEMENTS OF EARNINGS"

# (qname, value in M or None for an abstract) in presentation order.
BALANCE_SHEET = [
    (G + "LiabilitiesCurrentAbstract", None),
    (G + "CommercialPaper", 300),
    (G + "AccountsPayableCurrent", 10_000),
    (G + "LongTermDebtAndCapitalLeaseObligationsCurrent", 4_600),
    (G + "DeferredRevenueCurrent", 2_600),
    (G + "LiabilitiesCurrent", 30_000),
    (G + "LongTermDebtAndCapitalLeaseObligations", 48_500),
    (G + "OperatingLeaseLiabilityNoncurrent", 7_800),
    (G + "Liabilities", 90_000),
]
LABELS = {
    G + "CommercialPaper": "Short-term borrowings",
    G + "AccountsPayableCurrent": "Accounts payable",
    G + "LongTermDebtAndCapitalLeaseObligationsCurrent": "Current installments of long-term debt",
    G + "DeferredRevenueCurrent": "Deferred revenue",
    G + "LongTermDebtAndCapitalLeaseObligations": "Long-term debt, excluding current installments",
    G + "OperatingLeaseLiabilityNoncurrent": "Long-term operating lease liabilities",
    G + "Liabilities": "Total liabilities",
}


def _presentation(balance_sheet=BALANCE_SHEET):
    rows = [{"accession_no": ACC, "role_definition": IS, "tree_sequence": 1,
             "qname": G + "Revenues"}]
    for role in (BS_COPY, BS):  # the copy sorts after the face statement
        rows += [{"accession_no": ACC, "role_definition": role, "tree_sequence": i,
                  "qname": q} for i, (q, _v) in enumerate(balance_sheet, start=1)]
    return rows


def _facts(balance_sheet=BALANCE_SHEET, extra=()):
    rows = [{"fact_id": 100 + i, "accession_no": ACC, "qname": q, "value": v * M,
             "unit": "USD", "period_end": PE, "html_anchor": f"#f{100 + i}", "page_num": 50,
             "section_path": "Balance Sheet", "decimals": -6}
            for i, (q, v) in enumerate(balance_sheet) if v is not None]
    return rows + list(extra)


def _labels():
    rows = [{"concept": q, "cik_override": None, "label_role":
             "http://www.xbrl.org/2003/role/terseLabel", "label": t} for q, t in LABELS.items()]
    rows.append({"concept": G + "LiabilitiesCurrent", "cik_override": None,
                 "label_role": "http://www.xbrl.org/2003/role/totalLabel",
                 "label": "Total current liabilities"})
    return rows


class FakeDb:
    """Serves the three reads of ``read_filing_rows`` by the table each one names."""

    def __init__(self, presentation=None, facts=None, labels=None):
        self.presentation = _presentation() if presentation is None else presentation
        self.facts = _facts() if facts is None else facts
        self.labels = _labels() if labels is None else labels
        self.calls: list[dict] = []

    def fetch(self, _conn, sql: str, params: dict) -> list[dict]:
        self.calls.append(params)
        if "mv_presentation_network" in sql:
            return [r for r in self.presentation if r["accession_no"] in params["accessions"]]
        if "mv_financial_facts" in sql:
            return [r for r in self.facts if r["accession_no"] in params["accessions"]]
        if "concept_labels" in sql:
            return [r for r in self.labels if r["concept"] in params["qnames"]]
        raise AssertionError(sql)


def _read(db: FakeDb):
    return read_filing_rows(None, db.fetch, [(ACC, PE, CIK)], "USD")[(ACC, PE)]


def test_concept_names_follow_the_key_sides_form():
    # the FSDS form the key side uses: a standard prefix, else `ext`
    assert concept_name(G + "CommercialPaper") == "us-gaap:CommercialPaper"
    assert concept_name("https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full:Revenue") == (
        "ifrs-full:Revenue")
    assert concept_name(X + "DebtAndFinanceLeases") == "ext:DebtAndFinanceLeases"


def test_the_face_balance_sheet_is_read_once_in_order():
    filing = _read(FakeDb())
    assert filing.role == BS
    assert [ln["concept"] for ln in filing.lines] == [concept_name(q) for q, _v in BALANCE_SHEET]


def test_debt_is_the_sum_of_the_presented_borrowing_lines():
    from src.retriever.xbrl.defined_measure import defined_measure

    filing = _read(FakeDb())
    rule = defined_rules()["Debt"]
    m = defined_measure("Debt", measure_lines(filing, rule), filing.notes, rule, "USD")
    assert m["status"] == "defined"
    assert m["value"] == pytest.approx(53_400 * M, rel=1e-12)  # 300 + 4,600 + 48,500
    assert m["fact_ids"] == [101, 103, 106]
    assert [p["section"] for p in m["parts"]] == ["current", "current", "noncurrent"]


def test_the_current_total_places_the_lines_without_a_total_label():
    # No stored label names the total: the element itself ends the current section.
    db = FakeDb(labels=[r for r in _labels() if r["concept"] != G + "LiabilitiesCurrent"])
    filing = _read(db)
    rule = defined_rules()["Debt"]
    lines = measure_lines(filing, rule)
    assert next(ln for ln in lines if ln["concept"] == "us-gaap:LiabilitiesCurrent")[
        "label"] == "Total current liabilities"


def test_the_filers_own_label_comes_first():
    db = FakeDb(labels=[*_labels(), {
        "concept": G + "OperatingLeaseLiabilityNoncurrent", "cik_override": CIK,
        "label_role": "http://www.xbrl.org/2003/role/label", "label": "Operating leases"}])
    filing = _read(db)
    line = next(ln for ln in filing.lines
                if ln["concept"] == "us-gaap:OperatingLeaseLiabilityNoncurrent")
    assert line["labels"][0] == "Operating leases"


def test_rounded_note_duplicates_are_one_fact():
    # 49,000 at -9 is 48,958 at -6 rounded: one fact, the precise one (rounded-duplicates)
    notes = [
        {"fact_id": 900, "accession_no": ACC, "qname": G + "LongTermDebt", "value": 48_958 * M,
         "unit": "USD", "period_end": PE, "html_anchor": None, "page_num": None,
         "section_path": None, "decimals": -6},
        {"fact_id": 901, "accession_no": ACC, "qname": G + "LongTermDebt", "value": 49_000 * M,
         "unit": "USD", "period_end": PE, "html_anchor": None, "page_num": None,
         "section_path": None, "decimals": -9},
    ]
    filing = _read(FakeDb(facts=_facts(extra=notes)))
    assert [(n["fact_id"], n["value"]) for n in filing.notes] == [(900, 48_958 * M)]


def _candidate(fact_id: int, value: float) -> FactCandidate:
    return FactCandidate(
        fact_id=fact_id, value=value, unit="USD", period_type="instant", period_start=None,
        period_end=str(PE), concept=G + "LongTermDebt", concept_family="Debt",
        concept_pref_rank=1, segment_axis=None, segment_member=None, segment_label=None,
        section_path=None, citation=Citation(fact_id=fact_id, accession_no=ACC),
        concept_role="primary",
    )


def _lookup(db: FakeDb, monkeypatch) -> XbrlFactstoreLookup:
    lk = XbrlFactstoreLookup()

    @contextmanager
    def connect():
        yield None

    monkeypatch.setattr(lk, "_connect", connect)
    monkeypatch.setattr(lk, "_fetch", db.fetch)
    return lk


def test_a_defined_debt_lookup_answers_the_measure(monkeypatch):
    # today: LongTermDebt 51,400 from the notes; defined: 53,400 citing all three lines
    today = FactLookupResult("s1", LookupStatus.OK, value=51_400 * M, unit="USD",
                             citation=Citation(fact_id=900, accession_no=ACC),
                             candidates=[_candidate(900, 51_400 * M)])
    r = _lookup(FakeDb(), monkeypatch)._apply_defined_measure(today, {}, CIK, "Debt")
    assert r.status is LookupStatus.OK
    assert r.value == pytest.approx(53_400 * M, rel=1e-12)  # the hand sum
    assert r.citation.fact_id == 101
    assert r.citation.html_anchor == "#f101"
    assert r.sum_of == [101, 103, 106]
    assert r.definition["basis"] == "presented"
    assert "presented borrowing lines" in r.definition["disclosure"]
    assert r.to_dict()["definition"] == r.definition
    assert len(r.candidates) == 1  # kept for the ambiguity checks


def test_an_ambiguous_lookup_is_answered_by_its_definition(monkeypatch):
    today = FactLookupResult("s1", LookupStatus.AMBIGUOUS, value=51_400 * M, unit="USD",
                             candidates=[_candidate(900, 51_400 * M)])
    r = _lookup(FakeDb(), monkeypatch)._apply_defined_measure(today, {}, CIK, "Debt")
    assert r.status is LookupStatus.OK


def test_not_presented_keeps_todays_answer_with_the_disclosure(monkeypatch):
    sheet = [(q, v) for q, v in BALANCE_SHEET if "Debt" not in q and "CommercialPaper" not in q]
    db = FakeDb(presentation=_presentation(sheet), facts=_facts(sheet))
    today = FactLookupResult("s1", LookupStatus.OK, value=51_400 * M, unit="USD",
                             citation=Citation(fact_id=900, accession_no=ACC),
                             candidates=[_candidate(900, 51_400 * M)])
    r = _lookup(db, monkeypatch)._apply_defined_measure(today, {}, CIK, "Debt")
    assert (r.status, r.value, r.citation.fact_id) == (LookupStatus.OK, 51_400 * M, 900)
    assert r.definition["status"] == "not_presented"
    assert any("no balance-sheet line" in n.lower() for n in r.resolution_notes)


def test_a_unit_mismatch_refuses(monkeypatch):
    facts = _facts()
    for f in facts:
        if f["qname"] == G + "CommercialPaper":
            f["unit"] = "EUR"
    today = FactLookupResult("s1", LookupStatus.OK, value=51_400 * M, unit="USD",
                             citation=Citation(fact_id=900, accession_no=ACC),
                             candidates=[_candidate(900, 51_400 * M)])
    r = _lookup(FakeDb(facts=facts), monkeypatch)._apply_defined_measure(today, {}, CIK, "Debt")
    assert r.status is LookupStatus.NOT_FOUND
    assert r.value is None
    assert r.error


def test_no_candidate_means_no_column_and_no_change(monkeypatch):
    today = FactLookupResult("s1", LookupStatus.NOT_FOUND)
    db = FakeDb()
    assert _lookup(db, monkeypatch)._apply_defined_measure(today, {}, CIK, "Debt") is today
    assert db.calls == []


def test_a_defined_screen_ranks_the_measure_and_names_the_rest(monkeypatch):
    screen = XbrlFactstoreScreen()
    db = FakeDb()
    monkeypatch.setattr(screen, "_fetch", db.fetch)
    picks = [
        {"ticker": "HDX", "cik": CIK, "fiscal_year": 2025, "value": 51_400 * M,
         "accession_no": ACC, "period_end": PE, "fact_id": 900},
        {"ticker": "NOP", "cik": "0000000002", "fiscal_year": 2025, "value": 9 * M,
         "accession_no": "0000000002-26-000001", "period_end": PE, "fact_id": 950},
        {"ticker": "OLD", "cik": "0000000003", "fiscal_year": 2024, "value": 7 * M,
         "accession_no": "0000000003-25-000001", "period_end": PE, "fact_id": 960},
    ]
    reasons: list[dict] = []
    notes: list[str] = []
    out = screen._apply_defined_measures(None, picks, "Debt", 2025, notes, reasons)
    by = {r["ticker"]: r for r in out}
    assert by["HDX"]["value"] == pytest.approx(53_400 * M, rel=1e-12)  # the hand sum
    assert by["HDX"]["fact_id"] == 101
    assert by["HDX"]["component_fact_ids"] == {"part_1": 101, "part_2": 103, "part_3": 106}
    assert "NOP" not in by  # a filing with no lines read is never ranked by a fallback
    assert reasons == [{"ticker": "NOP", "reason": "metric_not_tagged"}]
    assert by["OLD"] is picks[2]  # another year is left to select_fiscal_year


def _defined_step() -> StepResult:
    definition = {
        "concept": "s4:deferred_revenue_total", "status": "defined", "basis": "note_total",
        "disclosure": "Defined as total deferred revenue: the filing's tagged total.",
        "parts": [{"fact_id": 7, "concept": "us-gaap:ContractWithCustomerLiability",
                   "value": 8_600 * M, "section": "note", "label": None}],
        "readings": [{"reading": "current_presented", "value": 6_600 * M,
                      "concept": "us-gaap:ContractWithCustomerLiabilityCurrent",
                      "fact_ids": [5]}],
    }
    return StepResult("s1", "lookup", StepStatus.OK, value=8_600 * M, unit="USD",
                      citation={"fact_id": 7}, definition=definition,
                      component_fact_ids=defined_fact_ids(definition))


def test_parts_and_readings_resolve_to_the_step():
    step = _defined_step()
    assert step.component_fact_ids == {"part_1": 7, "current_presented": 5}
    assert defined_fact_ids({"status": "not_presented"}) is None
    execution = {"steps": [step.to_dict()]}
    detail = {"statement": "Total deferred revenue was 8,600,000,000 USD; the presented "
                           "current amount is 6,600,000,000 USD.",
              "fact_ids": [7, 5], "chunk_ids": []}
    assert unresolved_citation_ids(detail, execution, []) == []
    assert unmatched_numbers(detail, execution, {"steps": []}, []) == []


def test_the_synthesizer_prompt_states_the_definition():
    prompt = get_xbrl_registry().resolve("synthesizer", provider="openai", series="gpt-5")
    assert '"definition"' in prompt.system.template
    assert '"disclosure"' in prompt.system.template
    prompt.system.format(llm_answer_format="{}")  # placeholders unchanged
