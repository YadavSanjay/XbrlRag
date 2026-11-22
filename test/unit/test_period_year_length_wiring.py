"""Wiring of the year-length note into the query pipeline (unlocked work of
docs/specs/period-year-length-answers.md, Requirement 6).

The executor's compute and cagr steps carry ``year_lengths`` from their lookups' cited
periods; the derived screen reads its component facts' periods and carries the ranked
members' entries; the pipeline appends a screen's sentence once and logs a decision event
when a compute note is missing from the answer. Periods are AAPL's stored revenue periods
(FY2024 364 days, FY2023 371 days); the database is faked at ``psycopg2.connect``.
"""

from __future__ import annotations

from unittest.mock import patch

import psycopg2

from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.plan_executor import PlanExecutor, cited_period
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
)
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen
from src.retriever.xbrl.year_length_answers import screen_year_length_text

CIK = 320193
# step id -> (fy, value, fact_id, stored start, stored end)
FACTS = {
    "r24": (2024, 391.035e9, 14262, "2023-10-01", "2024-09-29"),
    "r23": (2023, 383.285e9, 12096, "2022-09-25", "2023-10-01"),
}


def _candidate(fact_id: int, start: str, end: str) -> FactCandidate:
    return FactCandidate(
        fact_id=fact_id, value=None, unit="USD", period_type="duration", period_start=start,
        period_end=end, concept="us-gaap:Revenues", concept_family="Revenue",
        concept_pref_rank=1, segment_axis=None, segment_member=None, segment_label=None,
        section_path=None, citation=Citation(fact_id=fact_id),
    )


class _Store:
    """A lookup store answering FACTS; ``missing`` step ids are NOT_FOUND."""

    def __init__(self, missing: tuple[str, ...] = ()):
        self.missing = missing

    def lookup(self, step):
        if step["id"] in self.missing:
            return FactLookupResult(step_id=step["id"], status=LookupStatus.NOT_FOUND)
        _, value, fid, start, end = FACTS[step["id"]]
        # A second candidate of another period: only the cited one is read.
        other = _candidate(fid + 1, "2020-01-01", "2020-07-01")
        return FactLookupResult(
            step_id=step["id"], status=LookupStatus.OK, value=value, unit="USD",
            citation=Citation(fact_id=fid), confidence=0.9,
            candidates=[other, _candidate(fid, start, end)],
        )


def _lookup(sid: str) -> dict:
    return {"id": sid, "op": "lookup", "cik": CIK, "ticker": "AAPL", "fy": FACTS[sid][0],
            "concept_family": "Revenue", "fiscal_period": "FY", "dimensions": "NONE"}


GROWTH_PLAN = {"steps": [_lookup("r24"), _lookup("r23"),
                         {"id": "g", "op": "compute", "expr": "(r24 - r23) / r23"}]}
CAGR_PLAN = {"steps": [_lookup("r23"), _lookup("r24"),
                       {"id": "c", "op": "cagr", "start": "r23", "end": "r24"}]}


def test_cited_period_is_the_cited_candidates():
    result = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN).step("r24")
    assert cited_period(result) == ("2023-10-01", "2024-09-29")


def test_ok_compute_carries_the_year_lengths_and_logs_them():
    with patch("src.retriever.xbrl.plan_executor.log_event") as log:
        execution = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN)
    step = execution.step("g").to_dict()
    entries = step["year_lengths"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["current_step"], entry["prior_step"]) == ("r24", "r23")
    assert entry["fact_ids"] == [14262, 12096]
    assert (entry["days_current"], entry["days_prior"]) == (364, 371)
    assert "53-week" in entry["note"]
    events = [c.kwargs for c in log.call_args_list
              if c.args == ("numeric_lineage",) and c.kwargs.get("op") == "compute"]
    assert events[0]["year_lengths"] == entries
    # A lookup's to_dict never grows a period.
    assert "year_lengths" not in execution.step("r24").to_dict()


def test_ok_cagr_carries_the_year_lengths():
    execution = PlanExecutor(factstore_lookup=_Store()).execute(CAGR_PLAN)
    entries = execution.step("c").to_dict()["year_lengths"]
    assert [(e["current_step"], e["prior_step"]) for e in entries] == [("r24", "r23")]


def test_a_compute_that_is_not_ok_carries_none():
    execution = PlanExecutor(factstore_lookup=_Store(missing=("r23",))).execute(GROWTH_PLAN)
    step = execution.step("g")
    assert step.status.value == "ERROR"
    assert step.year_lengths == [] and "year_lengths" not in step.to_dict()


def test_verifier_evidence_shows_the_compute_notes():
    execution = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN).to_dict()
    by_step, by_fact = XbrlFaithfulnessVerifier._index_fact_steps(execution)
    evidence = XbrlFaithfulnessVerifier._resolve_fact_evidence(["g"], by_step, by_fact)
    assert evidence[0]["year_lengths"][0]["days_prior"] == 371


# ------------------------------------------------------------------------ derived screen
DERIVED = [
    {"ticker": "AAPL", "cik": "0000320193", "fiscal_year": 2024, "value": 0.0202,
     "metric_name": "RevenueGrowthPct", "formula": "growth",
     "component_fact_ids": {"revenue_current": 14262, "revenue_prior": 12096}},
    {"ticker": "MSFT", "cik": "0000789019", "fiscal_year": 2024, "value": 0.157,
     "metric_name": "RevenueGrowthPct", "formula": "growth",
     "component_fact_ids": {"revenue_current": "105742", "revenue_prior": "104052"}},
]
FACT_ROWS = {
    14262: ("2023-10-01", "2024-09-29"), 12096: ("2022-09-25", "2023-10-01"),
    105742: ("2023-07-01", "2024-07-01"), 104052: ("2022-07-01", "2023-07-01"),
}


class _Cursor:
    def __init__(self, as_dict):
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            rows: list[dict] = [{"present": False}]
        elif "entity_master" in sql:
            rows = [{"cik": r["cik"], "ticker": r["ticker"]} for r in DERIVED]
        elif "mv_derived_metrics" in sql:
            rows = [dict(r) for r in DERIVED]
        elif "mv_financial_facts" in sql:
            ids = (params or {}).get("fact_ids") or []
            rows = [{"fact_id": i, "unit": "USD", "period_start": FACT_ROWS[i][0],
                     "period_end": FACT_ROWS[i][1]} for i in ids if i in FACT_ROWS]
        else:
            rows = []
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def cursor(self, cursor_factory=None):
        return _Cursor(as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_year_length_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def test_derived_growth_screen_carries_the_unequal_member(monkeypatch):
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: _Conn())
    step = {"id": "s1", "op": "screen", "metric_kind": "derived:RevenueGrowthPct",
            "fy": 2024, "fiscal_period": "FY", "order": "desc"}
    result = XbrlFactstoreScreen(db_conn_params={}).screen(step)
    assert result.is_ok, result.resolution_notes
    assert [r["ticker"] for r in result.rows] == ["MSFT", "AAPL"]
    assert [(e["ticker"], e["fact_ids"]) for e in result.year_lengths] == [
        ("AAPL", [14262, 12096])
    ]
    assert result.to_dict()["year_lengths"] == result.year_lengths


# ------------------------------------------------------------------------------ pipeline
def _screen_step() -> dict:
    entry = {"ticker": "AAPL", "fact_ids": [14262, 12096], "days_current": 364,
             "days_prior": 371, "current_period": ("2023-10-01", "2024-09-28"),
             "prior_period": ("2022-09-25", "2023-09-30"), "note": "n"}
    return {"step_id": "s1", "op": "screen", "status": "OK", "rows": [],
            "year_lengths": [entry]}


def test_pipeline_appends_the_screen_sentence_once():
    steps = [_screen_step()]
    sentence = screen_year_length_text(steps[0]["year_lengths"])
    text = XbrlRetrievalPipeline._append_disclosures("MSFT grew fastest.", steps)
    assert text == "MSFT grew fastest. " + sentence
    assert XbrlRetrievalPipeline._append_disclosures(text, steps) == text


def test_missing_compute_note_is_a_decision_event():
    execution = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN).to_dict()
    note = execution["steps"][2]["year_lengths"][0]["note"]
    with patch("src.pipelines.xbrl.retrieval_pipeline.log_event") as log:
        missing = XbrlRetrievalPipeline._log_missing_year_length_notes(
            "Revenue grew 2.02%.", execution["steps"], [])
        stated = XbrlRetrievalPipeline._log_missing_year_length_notes(
            f"Revenue grew 2.02%. {note}", execution["steps"], [])
    assert missing == [{"step_id": "g", "fact_ids": [14262, 12096]}] and stated == []
    events = [c.kwargs for c in log.call_args_list if c.args == ("decision",)]
    assert len(events) == 1 and events[0]["point"] == "year_length_note"


def _note_events(summary: str, details: list[dict]) -> tuple[list[dict], list[dict]]:
    """Runs the note check on GROWTH_PLAN; returns (missing, logged decision events)."""
    steps = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN).to_dict()["steps"]
    with patch("src.pipelines.xbrl.retrieval_pipeline.log_event") as log:
        missing = XbrlRetrievalPipeline._log_missing_year_length_notes(summary, steps, details)
    events = [c.kwargs for c in log.call_args_list if c.args == ("decision",)]
    return missing, events


def _growth_note() -> str:
    execution = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN).to_dict()
    return execution["steps"][2]["year_lengths"][0]["note"]


def test_note_in_a_statement_citing_the_step_counts_as_stated():
    # The summary paraphrases; the compute statement citing step g carries it verbatim.
    details = [{"statement": f"Revenue grew 2.02%. {_growth_note()}", "fact_ids": ["g"]}]
    missing, events = _note_events("Revenue grew 2.02% (years differ in length).", details)
    assert missing == [] and events == []


def test_note_absent_from_summary_and_statements_is_a_decision_event():
    details = [{"statement": "Revenue grew 2.02%.", "fact_ids": ["g"]}]
    missing, events = _note_events("Revenue grew 2.02%.", details)
    assert missing == [{"step_id": "g", "fact_ids": [14262, 12096]}]
    assert len(events) == 1
    assert events[0]["point"] == "year_length_note"
    assert events[0]["decision"] == "note_missing_from_answer"
    assert events[0]["step_id"] == "g"


def test_note_only_in_a_statement_not_citing_the_step_is_a_decision_event():
    # The statement cites the prior-year lookup only, so its text does not vouch for g.
    details = [{"statement": f"FY2023 revenue was $383.3B. {_growth_note()}",
                "fact_ids": ["r23"]}]
    missing, events = _note_events("Revenue grew 2.02%.", details)
    assert missing == [{"step_id": "g", "fact_ids": [14262, 12096]}]
    assert len(events) == 1
