"""Fixtures for the set-b-restatement spec tests (docs/specs/set-b-restatement.md).

Real excerpts, committed under ``fixtures/``: byte copies of the restatement-s1 fixtures (their
provenance is recorded inside each file and in docs/specs/restatement-s1.md Notes; the copies'
SHA-256 is checked below):

- ``companyfacts.json``: SEC companyfacts entries of 11 filers (report forms filed from
  2021-06-01) for a few concepts, NetIncomeLoss, Assets and the two cover facts;
- ``dei.json``: the DEI of every corpus filing of those filers, from its iXBRL instance;
- ``fsds.json``: FSDS sub/num rows of a set of those filings.

Store rows (``mv_financial_facts`` shape) are built from the companyfacts entries of corpus
filings, as restatement-s1's Notes describe. The database, the filing store and the LLM provider
are the only I/O boundaries faked here. Nothing reads the network, the current date or anything
outside this directory. No held-out content is used.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.systems.base import CitedFilingResolver, FilingResolver
from src.eval.systems.long_context import LongContextSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SHA256 = {
    "companyfacts.json": "9ecd720efe49e1ae8af857003d3fc606499a69089c0c6af51528b9c9d42f5255",
    "dei.json": "2e261b57dd0843f2b4999df274ad4691bfd2d495972721f6c91a6552a8436a1f",
    "fsds.json": "15bfc9524dce3bb7f0e301742e8c2f5fb409adb76703c61db51490ec2491b536",
}
for _name, _digest in FIXTURE_SHA256.items():
    assert hashlib.sha256((FIXTURES / _name).read_bytes()).hexdigest() == _digest, _name

COMPANYFACTS: dict[str, dict] = json.loads((FIXTURES / "companyfacts.json").read_text("utf-8"))
DEI: dict[str, dict] = json.loads((FIXTURES / "dei.json").read_text("utf-8"))
FSDS: dict[str, Any] = json.loads((FIXTURES / "fsds.json").read_text("utf-8"))

CIK = {t: f"{doc['cik']:010d}" for t, doc in COMPANYFACTS.items()}

# The store's concept family of each tag (concept_mapping, rank 1 for these filers).
FAMILY = {
    "EarningsPerShareDiluted": "EarningsPerShareDiluted",
    "NetCashProvidedByUsedInFinancingActivities": "CashFromFinancing",
    "AccountsReceivableNetCurrent": "AccountsReceivable",
    "DepreciationDepletionAndAmortization": "DepreciationAndAmortization",
    "CommonStockDividendsPerShareDeclared": "DividendsPerShare",
    "Goodwill": "Goodwill",
    "BusinessExitCosts1": "RestructuringCharges",
    "RestructuringAndRelatedCostIncurredCost": "RestructuringCharges",
    "NetIncomeLoss": "NetIncome",
    "Assets": "TotalAssets",
    "EntityCommonStockSharesOutstanding": "SharesOutstanding",
}

# The corpus's Part III-only 10-K/As: filing_registry holds each superseded_by its 10-K
# (annual-amendment-handling C3), so the store marks it non-canonical.
PART_III = {
    "0000002488-26-000021": "0000002488-26-000018",   # AMD FY2025, filed the 10-K's day
    "0001193125-24-093194": "0001628280-24-006850",   # MRK FY2023
    "0001104659-25-042659": "0001628280-25-003063",   # TSLA FY2024
    "0001104659-26-053166": "0001628280-26-003952",   # TSLA FY2025
}

DEI_NAMESPACE = "http://xbrl.sec.gov/dei/2024"
ONE_DAY = dt.timedelta(days=1)


def next_day(value: str) -> str:
    """The store's next-midnight end of an inclusive SEC date."""
    return (dt.date.fromisoformat(value) + ONE_DAY).isoformat()


def local(qname: Any) -> str:
    """The local name of a QName (after the last ':')."""
    return str(qname).rsplit(":", 1)[-1]


# ----------------------------------------------------------------------------- FSDS oracle
def fsds_value(adsh: str, tag: str, ddate: str, qtrs: int) -> float:
    """The FSDS ``num`` value of one filing, tag, month-end ``ddate`` (YYYYMMDD) and ``qtrs``."""
    found = {float(r["value"]) for r in FSDS["num"]
             if r["adsh"] == adsh and r["tag"] == tag and r["ddate"] == ddate
             and int(r["qtrs"]) == qtrs}
    assert len(found) == 1, (adsh, tag, ddate, qtrs, found)
    return found.pop()


# The oracle points the tests quote as literals that FSDS also holds, checked here so a fixture
# that drifts from a literal fails loudly.
ORACLE_POINTS = [
    ("0001045810-25-000023", "EarningsPerShareDiluted", "20230131", 4, 0.17),
    ("0001045810-26-000021", "EarningsPerShareDiluted", "20240131", 4, 1.19),
    ("0001045810-26-000021", "EarningsPerShareDiluted", "20250131", 4, 2.94),
    ("0001045810-26-000021", "EarningsPerShareDiluted", "20260131", 4, 4.90),
    ("0001045810-23-000017", "EarningsPerShareDiluted", "20230131", 4, 1.74),
    ("0001045810-24-000029", "EarningsPerShareDiluted", "20240131", 4, 11.93),
    ("0001730168-24-000139", "EarningsPerShareDiluted", "20221031", 4, 2.65),
    ("0001730168-22-000118", "EarningsPerShareDiluted", "20221031", 4, 26.53),
    ("0000002488-25-000012", "AccountsReceivableNetCurrent", "20231231", 0, 4_323_000_000.0),
    ("0000002488-24-000012", "AccountsReceivableNetCurrent", "20231231", 0, 5_376_000_000.0),
    ("0000002488-26-000018", "AccountsReceivableNetCurrent", "20241231", 0, 6_192_000_000.0),
    ("0000002488-26-000018", "AccountsReceivableNetCurrent", "20251231", 0, 6_315_000_000.0),
]
for _adsh, _tag, _ddate, _qtrs, _value in ORACLE_POINTS:
    assert fsds_value(_adsh, _tag, _ddate, _qtrs) == pytest.approx(_value, abs=1e-9), _adsh


# ----------------------------------------------------------------------------- companyfacts
def companyfacts(ticker: str, corpus_only: bool = False) -> dict:
    """A fresh copy of one filer's companyfacts excerpt (only corpus filings' entries if asked)."""
    doc = copy.deepcopy(COMPANYFACTS[ticker])
    if corpus_only:
        for concepts in doc["facts"].values():
            for node in concepts.values():
                for unit in list(node["units"]):
                    node["units"][unit] = [e for e in node["units"][unit] if e["accn"] in DEI]
    return doc


def entry_value(ticker: str, tag: str, accn: str, end: str, unit: str) -> float:
    """The value one filing reports for one period end in the excerpt."""
    tax = "dei" if tag.startswith("Entity") else "us-gaap"
    vals = {e["val"] for e in COMPANYFACTS[ticker]["facts"][tax][tag]["units"][unit]
            if e["accn"] == accn and e["end"] == end}
    assert len(vals) == 1, (ticker, tag, accn, end, vals)
    return float(vals.pop())


# ----------------------------------------------------------------------------- store rows
def _duration_kind(start: str | None, stored_end: str) -> str:
    """mv_financial_facts.duration_kind: by the stored length in days."""
    if start is None:
        return "instant"
    days = (dt.date.fromisoformat(stored_end) - dt.date.fromisoformat(start)).days
    if days <= 100:
        return "quarter"
    if days <= 190:
        return "half"
    if days <= 285:
        return "nine_month"
    return "annual"


def _base_row(accn: str) -> dict:
    """The filing-level columns of a store row of filing ``accn``."""
    d = DEI[accn]
    return {
        "cik": d["cik"], "ticker": d["ticker"], "accession_no": accn,
        "fiscal_year": int(d["fiscal_year_focus"]), "fiscal_period": d["fiscal_period_focus"],
        "document_type": d["document_type"], "form_type": d["form"], "filed_date": d["filed"],
        "is_amendment": d["form"].endswith("/A"), "is_canonical_filing": accn not in PART_III,
        "document_period_end": d["document_period_end"],
    }


def _build_rows(ticker: str) -> list[dict]:
    doc = COMPANYFACTS[ticker]
    rows: list[dict] = []
    seq = 0
    for taxonomy, concepts in sorted(doc["facts"].items()):
        for tag, node in sorted(concepts.items()):
            for unit, es in sorted(node["units"].items()):
                for e in es:
                    accn = e["accn"]
                    if accn not in DEI:
                        continue
                    d = DEI[accn]
                    seq += 1
                    if taxonomy == "dei":
                        namespace = DEI_NAMESPACE
                    else:
                        namespace = d["us_gaap_namespace"] or "http://fasb.org/us-gaap/2025"
                    start = e.get("start")
                    end = next_day(e["end"])
                    rows.append({
                        **_base_row(accn),
                        "fact_id": 10_000_000 * (1 + sorted(CIK).index(ticker)) + seq,
                        "concept": f"{namespace}:{tag}", "concept_family": FAMILY.get(tag),
                        "concept_pref_rank": 1,
                        "value": None if e["val"] is None else float(e["val"]),
                        "unit": "shares*USD" if unit == "USD/shares" else unit,
                        "period_type": "duration" if start else "instant",
                        "period_start": start, "period_end": end,
                        "duration_kind": _duration_kind(start, end),
                        "is_current_period": e["end"] == d["document_period_end"],
                        "segment_axis": None, "segment_member": None, "segment_label": None,
                        "has_multiple_dimensions": False, "dimension_labels": None,
                    })
    return rows


_ROWS: dict[str, list[dict]] = {}


def store_rows(ticker: str) -> list[dict]:
    """The store's flat rows of one filer: one per companyfacts entry of a corpus filing."""
    if ticker not in _ROWS:
        _ROWS[ticker] = _build_rows(ticker)
    return [dict(r) for r in _ROWS[ticker]]


def rows_of(rows: Iterable[Mapping[str, Any]], tag: str, accn: str | None = None,
            end: str | None = None) -> list[Mapping[str, Any]]:
    """The rows of one concept (local name), optionally one filing and one inclusive SEC end."""
    return [r for r in rows if local(r["concept"]) == tag
            and (accn is None or r["accession_no"] == accn)
            and (end is None or r["period_end"] == next_day(end))]


def flat(cands: Iterable[Mapping[str, Any]], tag: str) -> list[Mapping[str, Any]]:
    """The flat candidates of one concept (local name)."""
    return [c for c in cands if not c["dimensional"] and local(c["concept"]) == tag]


# ----------------------------------------------------------------------------- dimensional rows
# Synthetic dimensional rows on two real NVDA 10-Ks (accessions and DEI dates are real; values,
# members and labels are hand-written). FY2025 own period: 2024-01-29 .. 2025-01-26 inclusive.
NVDA_FY2025 = "0001045810-25-000023"
NVDA_FY2026 = "0001045810-26-000021"
SEG_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
PROD_AXIS = "srt:ProductOrServiceAxis"


def dim_row(fact_id: int, accn: str, start: str, end: str, value: float, *,
            member: str = "nvda:ComputeAndNetworkingMember",
            label: str | None = "Compute & Networking [Member]",
            current: bool = True, multi: dict[str, str] | None = None,
            canonical: bool = True) -> dict:
    """One dimensional ``Revenues`` row (inclusive SEC dates in, store dates out)."""
    d = DEI[accn]
    return {
        **_base_row(accn), "is_canonical_filing": canonical,
        "fact_id": fact_id,
        "concept": f"{d['us_gaap_namespace']}:Revenues", "concept_family": "Revenue",
        "concept_pref_rank": 1, "value": float(value), "unit": "USD",
        "period_type": "duration", "period_start": start, "period_end": next_day(end),
        "duration_kind": "annual", "is_current_period": current,
        "segment_axis": SEG_AXIS, "segment_member": member, "segment_label": label,
        "has_multiple_dimensions": multi is not None, "dimension_labels": multi,
    }


def nvda_dim_rows() -> list[dict]:
    """The C3 dimensional rows: two current FY2025 rows and four that must not answer."""
    two_axes = {PROD_AXIS: "Data Center [Member]", SEG_AXIS: "Compute & Networking [Member]"}
    return [
        # FY2025 10-K, current period: the candidates.
        dim_row(91_000_001, NVDA_FY2025, "2024-01-29", "2025-01-26", 116_193_000_000.0),
        dim_row(91_000_002, NVDA_FY2025, "2024-01-29", "2025-01-26", 115_186_000_000.0,
                member="nvda:DataCenterMember", label=None, multi=two_axes),
        # FY2025 10-K, prior-year column: not the year's.
        dim_row(91_000_003, NVDA_FY2025, "2023-01-30", "2024-01-28", 47_405_000_000.0,
                current=False),
        # FY2026 10-K reporting the FY2025 period (a later-labelled filing): not used.
        dim_row(91_000_004, NVDA_FY2026, "2024-01-29", "2025-01-26", 116_000_000_000.0,
                current=False),
        # A non-canonical copy of the FY2025 row: never a candidate.
        dim_row(91_000_005, NVDA_FY2025, "2024-01-29", "2025-01-26", 1.0, canonical=False),
    ]


# ----------------------------------------------------------------------------- fake database
class FakeCursor:
    """A cursor over stored rows (spec Notes, fake database)."""

    def __init__(self, conn: FakeConn, as_dict: bool):
        self._conn = conn
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.queries.append((sql, params))
        ticker = params.get("ticker") if isinstance(params, Mapping) else None
        if not self._as_dict or ticker is None or "mv_financial_facts" not in sql:
            self._rows = []
            return
        self._rows = [dict(r) for r in self._conn.rows if r["ticker"] == ticker]

    def fetchall(self) -> list:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Iterator:
        return iter(list(self._rows))

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeConn:
    """A psycopg2-like connection over a list of store rows; records every query."""

    def __init__(self, rows: Sequence[dict]):
        self.rows = [dict(r) for r in rows]
        self.queries: list[tuple[str, Any]] = []

    def cursor(self, cursor_factory: Any = None, **kwargs: Any) -> FakeCursor:
        return FakeCursor(self, as_dict=cursor_factory is not None)

    def set_session(self, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------- long-context arm
# The LLM provider, the filing registry and the filing store are the I/O boundaries.
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

_DEAD_ENDPOINTS = {
    "QDRANT_HOST": "127.0.0.1", "QDRANT_PORT": "9", "QDRANT_HTTPS": "false",
    "RAGINFER_URL": "http://127.0.0.1:9", "RAGINFER_FALLBACK_LOCAL": "false",
    "XBRL_DB_HOST": "127.0.0.1", "XBRL_DB_PORT": "9",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "sk-spec-offline",
    "ANTHROPIC_API_KEY": "sk-ant-spec-offline",
}


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off (the DB boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _offline_env():
    """Points every backend endpoint at a closed local port and uses dummy API keys."""
    with pytest.MonkeyPatch.context() as mp:
        for key, value in _DEAD_ENDPOINTS.items():
            mp.setenv(key, value)
        yield


class FakeAdapter(ProviderAdapter):
    """Records each request and answers with a fixed reply."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(params)
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=self.reply, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=100, reasoning_tokens=0,
                              cached_prompt_tokens=0)

    def user_text(self, call: int = 0) -> str:
        return "\n".join(str(m.get("content", "")) for m in self.params[call]["messages"]
                         if m.get("role") == "user")


class FakeResolver(FilingResolver, CitedFilingResolver):
    """Both seams over fixed maps; records every call."""

    def __init__(self, keys: Mapping[tuple[str, int], str],
                 filings: Mapping[int, Mapping[str, Any]]) -> None:
        self.keys = dict(keys)
        self.filings = {int(k): dict(v) for k, v in filings.items()}
        self.calls: list[tuple] = []

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        self.calls.append(("canonical_key", ticker, fiscal_year))
        return self.keys.get((ticker, fiscal_year))

    def fact_filings(self, fact_ids: list[int]) -> dict[int, dict]:
        self.calls.append(("fact_filings", tuple(fact_ids)))
        return {int(i): dict(self.filings[int(i)]) for i in fact_ids if int(i) in self.filings}


class MemoryStore(FilingStore):
    """An in-memory filing store; records every read."""

    def __init__(self, objects: Mapping[str, str]) -> None:
        self.objects = {k: v.encode("utf-8") for k, v in objects.items()}
        self.reads: list[str] = []
        self._lock = threading.Lock()

    def write_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        with self._lock:
            self.reads.append(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter(())

    def localize_dir(self, prefix: str):
        raise RuntimeError("the in-memory store has no local directories")


def xhtml(text: str) -> str:
    """A minimal canonical-style XHTML document holding ``text``."""
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p>{text}</p></body></html>'


REPLY = json.dumps({"value": 0.17, "unit": "USD/shares", "rows": [], "refused": False,
                    "hedged": False, "cited_chunks": []})


def make_arm(keys: Mapping[tuple[str, int], str], filings: Mapping[int, Mapping[str, Any]],
             objects: Mapping[str, str]) -> tuple[LongContextSystem, FakeAdapter, FakeResolver,
                                                   MemoryStore]:
    """The oracle arm over a real gateway with a fake adapter, a fake resolver and store."""
    adapter = FakeAdapter(REPLY)
    gateway = LLMGateway(stage="long_context", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in PROVIDERS})
    resolver = FakeResolver(keys, filings)
    store = MemoryStore(objects)
    return LongContextSystem(gateway=gateway, resolver=resolver, store=store), adapter, \
        resolver, store


def make_case(case_id: str, ticker: str, fy: int, fact_ids: Sequence[int],
              value: float = 0.17) -> EvalCase:
    """A scalar lookup case citing ``fact_ids``."""
    return EvalCase(id=case_id, question=f"What was {ticker}'s diluted EPS in fiscal {fy}?",
                    kind="lookup", expected_value=value, ticker=ticker, fy=fy,
                    expected_fact_ids=list(fact_ids))


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis).

    Built from this module's globals: every spec directory has a module named ``conftest``, so
    ``sys.modules`` cannot name this one.
    """
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
