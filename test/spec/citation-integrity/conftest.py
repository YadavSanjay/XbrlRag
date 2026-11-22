"""Fixtures for the citation-integrity spec tests (docs/specs/citation-integrity.md).

Fakes sit only at I/O boundaries, as in the xbrlrag-run-halt spec:

- ``ScriptAdapter``: a fake provider adapter behind a real ``LLMGateway`` (real cost tracker,
  trace and run budget), installed as each LLM stage's ``_llm_client``. Each call plays the next
  scripted reply; when the script runs out it answers ``final``;
- ``FakeResolver`` (the entity resolver's SQL), ``FakeLookup`` and ``FakeScreen`` (the fact
  store), ``Untouchable`` for backends a test must not reach, ``NoCache``.

The values are real ``xbrl_db`` facts, read on 2026-11-07 (FY2024, fiscal_period FY, no
dimensions; see the spec's Notes). Chunk ids are UUID-shaped like ``_make_chunk_id``'s; chunk
texts are synthetic.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse, XbrlRagSynthesizer
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactLookupResult, LookupStatus
from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, ScreenStatus

API_KEY = "sk-spec-offline"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
_ENV_VARS = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_OPENAI_SERVICE_TIER", "XBRLRAG_QUERY_CACHE",
             "XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES", "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S")

# --------------------------------------------------------------------------- corpus values
# (ticker, family) -> (fact_id, value, unit, decimals); real xbrl_db facts, FY2024.
CIK = {"AAPL": "0000320193", "AMZN": "0001018724", "WMT": "0000104169", "MSFT": "0000789019"}
FACTS: dict[tuple[str, str], tuple[int, float, str, int]] = {
    ("AAPL", "Revenue"): (13253, 391_035_000_000.0, "USD", -6),
    ("AAPL", "GrossProfit"): (13265, 180_683_000_000.0, "USD", -6),
    ("AAPL", "NetIncome"): (13289, 93_736_000_000.0, "USD", -6),
    ("AAPL", "EarningsPerShareDiluted"): (13295, 6.08, "USD/shares", 2),
    ("AMZN", "Revenue"): (6854, 637_959_000_000.0, "USD", -6),
    ("AMZN", "NetIncome"): (6761, 59_248_000_000.0, "USD", -6),
    ("WMT", "Revenue"): (205006, 648_125_000_000.0, "USD", -6),
    ("WMT", "NetIncome"): (205048, 15_511_000_000.0, "USD", -6),
    ("MSFT", "Revenue"): (46957, 245_122_000_000.0, "USD", -6),
}
# AAPL FY2024 revenue by geography (mv_geographic_facts: fact, value, pct_of_total) and by
# product (mv_segment_matrix: fact, value).
AAPL_CHINA = (14256, 66_952_000_000.0, 0.17121741020624752)
AAPL_IPHONE = (13550, 201_183_000_000.0)
AAPL_SERVICES = (13562, 96_169_000_000.0)
AAPL_COST_OF_REVENUE = 13262  # 210,352,000,000; revenue - cost = 180,683,000,000

CHUNK_AAPL_MDNA = "5b0d9c1e-7a1f-5b8e-9d0c-3f2a1b4c6d7e"
CHUNK_AMZN_MDNA = "8e2f4a6b-1c3d-5e7f-8a9b-0c1d2e3f4a5b"
CHUNK_TEXT_AAPL = ("Services net sales increased during 2024 compared to 2023 due primarily to "
                   "higher net sales from advertising, the App Store and cloud services. "
                   "Services gross margin percentage was 73.9% in 2024.")
CHUNK_TEXT_AMZN = ("AWS segment sales increased 19% year-over-year, primarily driven by "
                   "increased customer usage.")


def fact(ticker: str, family: str) -> tuple[int, float, str, int]:
    return FACTS[(ticker, family)]


# --------------------------------------------------------------------------- environment


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the database boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _clean_env():
    """Clears the profile, cache and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


# --------------------------------------------------------------------------- LLM boundary


class ScriptAdapter(ProviderAdapter):
    """Plays one scripted reply per call; when the script runs out, every call answers *final*."""

    def __init__(self, final: str, script: list[str] | None = None) -> None:
        self.provider = "fake"
        self.final = final
        self.script = list(script or [])
        self.invoked = 0
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.invoked += 1
            self.params.append(params)
            return self.script.pop(0) if self.script else self.final

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def gateway(stage: str, adapter: ScriptAdapter) -> LLMGateway:
    return LLMGateway(stage=stage, cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda s: None,
                      adapters={p: adapter for p in PROVIDERS})


def supported_reply(n: int = 64) -> str:
    """The verifier LLM's reply: every index SUPPORTED (indices it was not sent are ignored)."""
    return json.dumps({"verdicts": [{"index": i, "verdict": "SUPPORTED",
                                     "reason": "matches the cited evidence"} for i in range(n)]})


def make_verifier(reply: str | None = None) -> tuple[XbrlFaithfulnessVerifier, ScriptAdapter]:
    """The real verifier, its LLM answering *reply* (default: everything SUPPORTED)."""
    verifier = XbrlFaithfulnessVerifier(api_key=API_KEY)
    adapter = ScriptAdapter(reply or supported_reply())
    verifier._llm_client = gateway("verifier", adapter)
    return verifier, adapter


def prompt_text(adapter: ScriptAdapter) -> str:
    """Everything the adapter was sent, as one string (statements appear JSON-escaped)."""
    return json.dumps(adapter.params, default=str)


def was_sent(adapter: ScriptAdapter, statement: str) -> bool:
    """Whether *statement* reached the LLM (its JSON-escaped form appears in a prompt)."""
    escaped = json.dumps(statement)[1:-1]
    text = prompt_text(adapter)
    return escaped in text or json.dumps(escaped)[1:-1] in text


# --------------------------------------------------------------------------- recorded results


def lookup_step(step_id: str, ticker: str, family: str, *, decimals: bool = True) -> dict:
    """A lookup step as ``StepResult.to_dict()`` holds it, for a real fact."""
    fid, value, unit, dec = fact(ticker, family)
    step = {"step_id": step_id, "op": "lookup", "status": "OK", "value": value, "unit": unit,
            "citation": {"fact_id": fid, "accession_no": None, "html_anchor": None,
                         "page_num": None, "section_path": None},
            "confidence": 1.0, "linked_chunk_ids": [], "error": None}
    if decimals:
        step["decimals"] = dec
    return step


def compute_step(step_id: str, value: float) -> dict:
    return {"step_id": step_id, "op": "compute", "status": "OK", "value": value, "unit": None,
            "citation": None, "confidence": 0.0, "linked_chunk_ids": [], "error": None}


def row(ticker: str, fid: int, value: float, *, rank: int | None = None,
        pct: float | None = None, label: str | None = None) -> dict:
    out = {"ticker": ticker, "cik": CIK.get(ticker), "fiscal_year": 2024, "value": value,
           "unit": "USD", "pct_of_total": pct, "label": label or ticker, "fact_id": str(fid),
           "citation": {"fact_id": fid, "accession_no": None, "html_anchor": None,
                        "page_num": None, "section_path": None}}
    if rank is not None:
        out["rank"] = rank
    return out


def rows_step(step_id: str, op: str, rows: list[dict]) -> dict:
    return {"step_id": step_id, "op": op, "status": "OK", "value": None, "unit": None,
            "citation": None, "confidence": 1.0, "linked_chunk_ids": [], "error": None,
            "rows": rows}


def narrative_step(step_id: str = "n1") -> dict:
    return {"step_id": step_id, "op": "narrative", "status": "OK", "value": None, "unit": None,
            "citation": None, "confidence": 1.0, "linked_chunk_ids": [], "error": None}


def chunk(chunk_id: str, text: str) -> dict:
    return {"chunk_id": chunk_id, "text": text, "section_path": "Item 7. MD&A"}


def detail(statement: str, fact_ids: list | None = None, chunk_ids: list | None = None) -> dict:
    return {"statement": statement, "fact_ids": list(fact_ids or []),
            "chunk_ids": list(chunk_ids or [])}


def response(details: list[dict], steps: list[dict], *, plan_steps: list[dict] | None = None,
             chunks: list[dict] | None = None) -> XbrlRagResponse:
    """A recorded synthesizer response (before the verifier)."""
    return XbrlRagResponse(
        query="recorded question", answer_summary="recorded summary.",
        answer_details=details, unanswered_parts=[], metadata={"fully_answered": True},
        plan={"steps": plan_steps or []},
        execution_result={"status": "OK", "steps": steps, "bindings": {}, "error": None},
        narrative_chunks=chunks or [])


def margin_plan(prefix: str, ticker: str) -> list[dict]:
    """Plan steps of a net-margin compute over two lookups (ids ``r_<p>``, ``n_<p>``, ``m_<p>``)."""
    return [
        {"id": f"r_{prefix}", "op": "lookup", "cik": CIK[ticker], "fy": 2024,
         "concept_family": "Revenue", "dimensions": "NONE", "section_hint": None},
        {"id": f"n_{prefix}", "op": "lookup", "cik": CIK[ticker], "fy": 2024,
         "concept_family": "NetIncome", "dimensions": "NONE", "section_hint": None},
        {"id": f"m_{prefix}", "op": "compute", "expr": f"n_{prefix} / r_{prefix}"},
    ]


def margin_steps(prefix: str, ticker: str) -> list[dict]:
    """The executed steps of ``margin_plan``; the margin is computed here in closed form."""
    rev, ni = fact(ticker, "Revenue")[1], fact(ticker, "NetIncome")[1]
    return [lookup_step(f"r_{prefix}", ticker, "Revenue"),
            lookup_step(f"n_{prefix}", ticker, "NetIncome"),
            compute_step(f"m_{prefix}", ni / rev)]


LINKED_ONLY_CHUNK = "c0ffee00-0000-5000-8000-000000000001"  # a step's linked chunk, not retrieved


def apple_evidence() -> tuple[list[dict], list[dict], list[dict]]:
    """(steps, plan steps, retrieved chunks) of one recorded Apple FY2024 query.

    Steps: lookups ``r_aapl`` (revenue, linked to an unretrieved chunk), ``n_aapl`` (net
    income), ``g_aapl`` (gross profit), ``e_aapl`` (diluted EPS); compute ``m_aapl`` = n / r;
    ``gp_der``, gross profit derived from revenue and cost of revenue (no citation, two
    component facts); ``r_msft``, a NOT_FOUND lookup; screen ``s1`` (FY2024 revenue, top
    three) and ``s2`` (Apple's China share); breakdown ``b1`` (iPhone, Services); narrative
    ``n1``.
    """
    rev = lookup_step("r_aapl", "AAPL", "Revenue")
    rev["linked_chunk_ids"] = [LINKED_ONLY_CHUNK]
    steps = margin_steps("aapl", "AAPL")
    steps[0] = rev
    steps += [
        lookup_step("g_aapl", "AAPL", "GrossProfit"),
        lookup_step("e_aapl", "AAPL", "EarningsPerShareDiluted"),
        {"step_id": "gp_der", "op": "lookup", "status": "OK", "value": 180_683_000_000.0,
         "unit": "USD", "citation": None, "confidence": 1.0, "linked_chunk_ids": [],
         "error": None,
         "component_fact_ids": {"revenue": 13253, "cost_of_revenue": AAPL_COST_OF_REVENUE}},
        {"step_id": "r_msft", "op": "lookup", "status": "NOT_FOUND", "value": None,
         "unit": None, "citation": None, "confidence": 0.0, "linked_chunk_ids": [],
         "error": "no fact"},
        rows_step("s1", "screen", [
            row("WMT", 205006, fact("WMT", "Revenue")[1], rank=1),
            row("AMZN", 6854, fact("AMZN", "Revenue")[1], rank=2),
            row("AAPL", 13253, fact("AAPL", "Revenue")[1], rank=3)]),
        rows_step("s2", "screen", [
            row("AAPL", AAPL_CHINA[0], AAPL_CHINA[1], rank=1, pct=AAPL_CHINA[2],
                label="CHINA")]),
        rows_step("b1", "breakdown", [
            row("AAPL", AAPL_IPHONE[0], AAPL_IPHONE[1], label="iPhone [Member]"),
            row("AAPL", AAPL_SERVICES[0], AAPL_SERVICES[1], label="Service [Member]")]),
        narrative_step("n1"),
    ]
    plan_steps = margin_plan("aapl", "AAPL") + [
        {"id": "g_aapl", "op": "lookup"}, {"id": "e_aapl", "op": "lookup"},
        {"id": "gp_der", "op": "lookup"}, {"id": "r_msft", "op": "lookup"},
        {"id": "s1", "op": "screen"}, {"id": "s2", "op": "screen"},
        {"id": "b1", "op": "breakdown"}, {"id": "n1", "op": "narrative"},
    ]
    chunks = [chunk(CHUNK_AAPL_MDNA, CHUNK_TEXT_AAPL)]
    return steps, plan_steps, chunks


# --------------------------------------------------------------------------- pipeline fakes


class FakeResolver:
    """The SQL boundary of ``EntityExtractor``: tickers resolve exactly, every period is 2024."""

    def resolve_companies(self, mentions: list[str]) -> list[ResolvedCompany]:
        return [ResolvedCompany(mention=m, cik=CIK[m], ticker=m, company_name=m,
                                resolution="exact_ticker", similarity=1.0) for m in mentions]

    def resolve_periods(self, mentions: list[str],
                        companies: list[ResolvedCompany] | None = None) -> list[ResolvedPeriod]:
        return [ResolvedPeriod(mention=m, fiscal_year=2024) for m in mentions]


class FakeLookup:
    """The fact store: a lookup step resolves to the real fact for its (cik, family)."""

    def __init__(self, ids: dict[int, int]) -> None:
        self.ids = ids
        self.by_cik = {(CIK[t], fam): v for (t, fam), v in FACTS.items()}

    def lookup(self, step: dict) -> FactLookupResult:
        fid, value, unit, _ = self.by_cik[(step["cik"], step["concept_family"])]
        return FactLookupResult(step_id=step.get("id"), status=LookupStatus.OK, value=value,
                                unit=unit, citation=Citation(fact_id=self.ids[fid]),
                                confidence=1.0)


class FakeScreen:
    """The cross-filer screen: FY2024 revenue, top three (real values)."""

    def __init__(self, ids: dict[int, int]) -> None:
        self.ids = ids

    def screen(self, step: dict) -> ScreenResult:
        top = [("WMT", *fact("WMT", "Revenue")[:2]), ("AMZN", *fact("AMZN", "Revenue")[:2]),
               ("AAPL", *fact("AAPL", "Revenue")[:2])]
        rows = [row(t, self.ids[fid], v, rank=i) for i, (t, fid, v) in enumerate(top, start=1)]
        return ScreenResult(step_id=step.get("id"), status=ScreenStatus.OK, rows=rows)


class Untouchable:
    """A backend the question must never reach."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"the {self._name} backend was touched ({attr})")


class NoCache:
    enabled = False


# --------------------------------------------------------------------------- compound scenario

COMPOUND = ("Which three companies had the highest revenue in fiscal 2024, and what was the "
            "net margin of the top two?")
SQ1 = "Which three companies had the highest revenue in fiscal 2024?"
SQ2 = "What was the net margin of <<TOP:sq1:2>> in fiscal 2024?"
INVENTED = 9_000_000_001  # resolves to nothing in either sub-query


def _real_ids() -> list[int]:
    return sorted({v[0] for v in FACTS.values()})


class Compound:
    """One real pipeline answering COMPOUND, and the hand-built expected answer.

    Sub-query 1 is a screen (step ``s1``) of FY2024 revenue: WMT, AMZN, AAPL. Sub-query 2
    depends on it (``<<TOP:sq1:2>>`` → "WMT, AMZN") and computes each net margin from two
    lookups. ``order1``/``order2`` permute each synthesizer reply's statements; ``ids`` renames
    every real fact id consistently.
    """

    # (statement, fact_ids, chunk_ids, kept?) per sub-query; ids written with real fact ids.
    SQ1_DETAILS = [
        # 648,125,000,000 stated at 0.1 billion
        ("Walmart ranked first with fiscal 2024 revenue of $648.1 billion.", [205006], True),
        ("Amazon was second with revenue of $637,959 million.", [6854], True),
        # a real fact plus an invented id: rule 1 drops it
        ("Apple ranked third at $391.0 billion.", [13253, INVENTED], False),
    ]
    SQ2_DETAILS = [
        # 15,511 / 648,125 = 0.023932 -> 2.39 %, stated at 0.1 pp
        ("Walmart's net margin was 2.4%.", ["m_wmt"], True),
        # 59,248 / 637,959 = 0.092871 -> 9.29 %; 59,248 million is an input of m_amzn
        ("Amazon's net margin was 9.3%, on net income of $59.2 billion.", ["m_amzn"], True),
        # Walmart's revenue cited to Amazon's revenue step: rule 3 drops it
        ("Amazon's revenue was $648.1 billion.", ["r_amzn"], False),
        # Apple's fact belongs to sub-query 1 only: rule 1 drops it
        ("Apple's revenue was $391.0 billion.", [13253], False),
        # the screen step s1 belongs to sub-query 1 only: rule 1 drops it
        ("Walmart had the highest revenue of the companies screened.", ["s1"], False),
        # 9.287 - 2.393 = 6.894: the synthesizer's own arithmetic, no cited value: rule 3
        ("Amazon's net margin exceeded Walmart's by 6.9 percentage points.",
         ["m_amzn", "m_wmt"], False),
    ]

    def __init__(self, order1: list[int] | None = None, order2: list[int] | None = None,
                 ids: dict[int, int] | None = None) -> None:
        self.ids = ids or {i: i for i in _real_ids()}
        self.ids.setdefault(INVENTED, INVENTED)
        order1 = order1 if order1 is not None else list(range(len(self.SQ1_DETAILS)))
        order2 = order2 if order2 is not None else list(range(len(self.SQ2_DETAILS)))
        sq1 = [self.SQ1_DETAILS[i] for i in order1]
        sq2 = [self.SQ2_DETAILS[i] for i in order2]

        def ids_of(raw: list) -> list[str]:
            return [str(self.ids[x]) if isinstance(x, int) else x for x in raw]

        def reply(details: list[tuple]) -> str:
            return json.dumps({
                "answer_summary": "recorded summary.",
                "answer_details": [{"statement": s, "fact_ids": ids_of(f), "chunk_ids": []}
                                   for s, f, _ in details],
                "unanswered_parts": [], "metadata": {"fully_answered": True}})

        self.expected = (
            [{"statement": s, "fact_ids": ids_of(f), "chunk_ids": [], "subquery_id": "sq1"}
             for s, f, keep in sq1 if keep]
            + [{"statement": s, "fact_ids": ids_of(f), "chunk_ids": [], "subquery_id": "sq2"}
               for s, f, keep in sq2 if keep])
        self.dropped = [s for s, _, keep in sq1 + sq2 if not keep]

        decomposer = json.dumps({"subqueries": [
            {"id": "sq1", "query": SQ1, "depends_on": []},
            {"id": "sq2", "query": SQ2, "depends_on": ["sq1"]}]})
        router = [
            json.dumps({"intent": "NUMERIC_SCREEN", "company_mentions": [],
                        "period_mentions": ["fiscal 2024"], "concept_phrases": ["revenue"],
                        "dimension_hint_present": False}),
            json.dumps({"intent": "NUMERIC_COMPUTE", "company_mentions": ["WMT", "AMZN"],
                        "period_mentions": ["fiscal 2024"],
                        "concept_phrases": ["net income", "revenue"],
                        "dimension_hint_present": False}),
        ]
        planner = [
            json.dumps({"steps": [{"id": "s1", "op": "screen", "concept_family": "Revenue",
                                   "fy": 2024, "dimension": "NONE", "metric_kind": "value",
                                   "order": "desc", "top_n": 3}]}),
            json.dumps({"steps": margin_plan("wmt", "WMT") + margin_plan("amzn", "AMZN")}),
        ]
        self.adapters = {
            "decomposer": ScriptAdapter(decomposer),
            "router": ScriptAdapter(router[-1], router),
            "plan_generator": ScriptAdapter(planner[-1], planner),
            "synthesizer": ScriptAdapter(reply(sq2), [reply(sq1), reply(sq2)]),
            "verifier": ScriptAdapter(supported_reply()),
        }
        resolver = FakeResolver()
        stages = {
            "decomposer": QueryDecomposer(api_key=API_KEY),
            "router": IntentRouter(api_key=API_KEY, entity_extractor=resolver),
            "plan_generator": PlanGenerator(api_key=API_KEY),
            "synthesizer": XbrlRagSynthesizer(api_key=API_KEY),
            "verifier": XbrlFaithfulnessVerifier(api_key=API_KEY),
        }
        for name, stage in stages.items():
            stage._llm_client = gateway(name, self.adapters[name])
        self.pipeline = XbrlRetrievalPipeline(
            entity_extractor=resolver,
            intent_router=stages["router"],
            query_decomposer=stages["decomposer"],
            plan_generator=stages["plan_generator"],
            factstore_lookup=FakeLookup(self.ids),
            factstore_screen=FakeScreen(self.ids),
            factstore_breakdown=Untouchable("breakdown"),
            narrative_retriever=Untouchable("narrative"),
            synthesizer=stages["synthesizer"],
            faithfulness_verifier=stages["verifier"],
            persist_audit=False,
            query_cache=NoCache(),
        )


@pytest.fixture(scope="session")
def ci():
    """The builders and constants above, as attributes (spec tests cannot import conftest)."""
    import types

    names = ("API_KEY", "CIK", "FACTS", "AAPL_CHINA", "AAPL_IPHONE", "AAPL_SERVICES",
             "AAPL_COST_OF_REVENUE", "CHUNK_AAPL_MDNA", "CHUNK_AMZN_MDNA", "CHUNK_TEXT_AAPL",
             "CHUNK_TEXT_AMZN", "LINKED_ONLY_CHUNK", "COMPOUND", "INVENTED", "Compound",
             "ScriptAdapter", "apple_evidence", "chunk", "compute_step", "detail", "fact",
             "lookup_step", "make_verifier", "margin_plan", "margin_steps", "narrative_step",
             "prompt_text", "response", "row", "rows_step", "supported_reply", "was_sent")
    g = globals()
    return types.SimpleNamespace(**{n: g[n] for n in names})
