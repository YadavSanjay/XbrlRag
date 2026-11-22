"""Fixtures for the dimensional-s1 spec tests (docs/specs/dimensional-s1.md).

Real excerpts, committed under ``fixtures/`` (provenance in each file and in the spec's Notes):

- ``fsds_segments.json``: SEC Financial Statement Data Sets ``sub`` and ``num`` rows, segments
  kept, of six corpus 10-Ks: Intel's FY2022-FY2024 (0000050863-23-000006, -24-000010,
  -25-000009) at the FY2022 period, NVIDIA's FY2023-FY2025 (0001045810-23-000017, -24-000029,
  -25-000023) at the FY2023 period; revenue and operating income, annual, USD;
- ``dei.json``: each filing's DEI from its iXBRL instance (copied from restatement-s1).

Store rows (``mv_financial_facts`` / ``mv_financial_facts_multidim`` shape) are built from the
``num`` rows as the spec's Notes describe. The database is the only I/O boundary: ``FakeConn``
replaces ``psycopg2.connect`` and follows the spec's fake-database contract. Nothing here reads
the network, the current date or anything outside this directory.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_breakdown import XbrlFactstoreBreakdown
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FSDS: dict[str, Any] = json.loads((FIXTURES / "fsds_segments.json").read_text("utf-8"))
DEI: dict[str, dict] = json.loads((FIXTURES / "dei.json").read_text("utf-8"))

ONE_DAY = dt.timedelta(days=1)

# The filings, by role. The "own" filing of a year is the one labelled with it.
INTC_OWN, INTC_FY23, INTC_FY24 = (
    "0000050863-23-000006", "0000050863-24-000010", "0000050863-25-000009")
NVDA_OWN, NVDA_FY24, NVDA_FY25 = (
    "0001045810-23-000017", "0001045810-24-000029", "0001045810-25-000023")
TICKER = {"0000050863": "INTC", "0001045810": "NVDA"}
CIK = {t: c for c, t in TICKER.items()}
# The annual period every row of the excerpt reports (inclusive SEC dates). FSDS rounds
# ``ddate`` to the month end; the exact dates are the own filing's DEI DocumentPeriodEndDate
# and the start companyfacts gives for the same period (restatement-s1 fixtures:
# INTC NetCashProvidedByUsedInFinancingActivities 2021-12-26..2022-12-31, NVDA
# EarningsPerShareDiluted 2022-01-31..2023-01-29).
PERIOD = {"INTC": ("2021-12-26", "2022-12-31"), "NVDA": ("2022-01-31", "2023-01-29")}
YEAR = {"INTC": 2022, "NVDA": 2023}

FAMILY = {"Revenues": "Revenue",
          "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
          "OperatingIncomeLoss": "OperatingIncome"}
# FSDS names an axis without its "Axis" suffix and namespace (readme 2.4, ``segments``).
AXIS_QNAME = {
    "BusinessSegments": "us-gaap:StatementBusinessSegmentsAxis",
    "Geographical": "srt:StatementGeographicalAxis",
    "ProductOrService": "srt:ProductOrServiceAxis",
    "ConsolidationItems": "srt:ConsolidationItemsAxis",
    "ConsolidatedEntities": "srt:ConsolidatedEntitiesAxis",
}
# The country members' labels (the ISO code is the member's local name, ``country:XX``).
COUNTRY_LABEL = {"TW": "TAIWAN", "US": "UNITED STATES", "SG": "SINGAPORE"}
GEO_AXES = ("StatementGeographicalAxis",)


def local(qname: Any) -> str:
    """The local name of a QName (after the last ':' or '}')."""
    return re.split(r"[:}]", str(qname))[-1]


def next_day(value: str) -> str:
    """The store's next-midnight end of an inclusive SEC date."""
    return (dt.date.fromisoformat(value) + ONE_DAY).isoformat()


def member_qname(ticker: str, axis: str, name: str) -> str:
    """The member QName FSDS's ``Axis=Name`` stands for (country codes in ``country:``)."""
    if axis == "Geographical" and re.fullmatch(r"[A-Z]{2}", name):
        return f"country:{name}"
    if name in ("OperatingSegments", "CorporateNonSegment", "IntersegmentElimination"):
        return f"us-gaap:{name}Member"
    return f"{ticker.lower()}:{name}Member"


def member_label(axis: str, name: str) -> str:
    """A synthetic label: the name's words plus ``[Member]`` (country labels by code).

    The labels are not oracle values: they only let the lookup's and screens' label
    matching find the member.
    """
    if axis == "Geographical" and name in COUNTRY_LABEL:
        return COUNTRY_LABEL[name]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return f"{words} [Member]"


def parse_segments(text: str) -> list[tuple[str, str]]:
    """FSDS ``segments`` (``Axis=Name;...``) as ``(axis, name)`` pairs, in file order."""
    pairs = []
    for part in text.split(";"):
        if part:
            axis, name = part.split("=", 1)
            pairs.append((axis, name))
    return pairs


# ----------------------------------------------------------------------------- store rows
def _build_rows() -> list[dict]:
    rows: list[dict] = []
    for i, n in enumerate(sorted(FSDS["num"], key=lambda r: (r["adsh"], r["tag"],
                                                             r["segments"]))):
        accn = n["adsh"]
        d = DEI[accn]
        ticker = TICKER[d["cik"]]
        start, end = PERIOD[ticker]
        dims = parse_segments(n["segments"])
        concept = f"{d['us_gaap_namespace']}:{n['tag']}"
        row = {
            "fact_id": (1 if ticker == "INTC" else 2) * 1_000_000 + i,
            "cik": d["cik"], "ticker": ticker, "accession_no": accn,
            "fiscal_year": int(d["fiscal_year_focus"]),
            "fiscal_period": d["fiscal_period_focus"], "form_type": d["form"],
            "filed_date": d["filed"], "is_amendment": d["form"].endswith("/A"),
            "is_canonical_filing": True,
            "document_period_end": d["document_period_end"],
            "concept": concept, "concept_family": FAMILY[n["tag"]],
            "concept_pref_rank": 1, "concept_role": None, "substitution_code": None,
            "substitution_note": None, "presented": False,
            "value": float(n["value"]), "unit": "USD",
            "period_type": "duration", "period_start": start, "period_end": next_day(end),
            "duration_kind": "annual",
            "is_current_period": end == d["document_period_end"],
            "section_path": None, "html_anchor": None, "page_num": None,
            "linked_chunk_ids": [], "decimals": -6,
        }
        if not dims:
            row.update(segment_axis=None, segment_member=None, segment_label=None,
                       has_multiple_dimensions=False, dimensions=None,
                       dimension_labels=None)
        else:
            # The flat view files a fact under its first dimension (modelled as FSDS's
            # first pair) and flags a multi-dimension one.
            axis, name = dims[0]
            row.update(segment_axis=AXIS_QNAME[axis],
                       segment_member=member_qname(ticker, axis, name),
                       segment_label=member_label(axis, name),
                       has_multiple_dimensions=len(dims) > 1)
            if len(dims) > 1:
                row["dimensions"] = {AXIS_QNAME[a]: member_qname(ticker, a, m)
                                     for a, m in dims}
                row["dimension_labels"] = {AXIS_QNAME[a]: member_label(a, m)
                                           for a, m in dims}
            else:
                row["dimensions"] = None
                row["dimension_labels"] = None
        rows.append(row)
    return rows


_ROWS = _build_rows()


def store_rows(ticker: str | None = None, family: str | None = None,
               accessions: Iterable[str] | None = None) -> list[dict]:
    """Fresh copies of the store's rows, optionally of one filer, family and filings."""
    keep = None if accessions is None else set(accessions)
    return [dict(r) for r in _ROWS
            if (ticker is None or r["ticker"] == ticker)
            and (family is None or r["concept_family"] == family)
            and (keep is None or r["accession_no"] in keep)]


def flat_view(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Rows as ``mv_financial_facts`` serves them: no ``dimensions`` columns."""
    return [{k: v for k, v in r.items() if k not in ("dimensions", "dimension_labels",
                                                      "dimension_count")}
            for r in rows]


def multidim_view(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The multi-dimension rows as ``mv_financial_facts_multidim`` serves them."""
    out = []
    for r in rows:
        if not r.get("dimensions"):
            continue
        row = dict(r)
        row.update(segment_axis=None, segment_member=None, segment_label=None,
                   concept_family=None, dimension_count=len(r["dimensions"]))
        out.append(row)
    return out


def dims_of(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """A store row's full dimension set by local name, from the fixture (oracle side)."""
    if row.get("dimensions"):
        return tuple(sorted((local(a), local(m)) for a, m in row["dimensions"].items()))
    if row.get("segment_axis") is None:
        return ()
    return ((local(row["segment_axis"]), local(row["segment_member"])),)


def find(rows: Iterable[Mapping[str, Any]], accession: str, tag: str,
         segments: str) -> dict:
    """The one store row of a filing, tag (local name) and FSDS ``segments`` text."""
    ticker = TICKER[accession[:10]]
    want = tuple(sorted((local(AXIS_QNAME[a]), local(member_qname(ticker, a, m)))
                        for a, m in parse_segments(segments)))
    hits = [r for r in rows if r["accession_no"] == accession and local(r["concept"]) == tag
            and dims_of(r) == want]
    assert len(hits) == 1, (accession, tag, segments, len(hits))
    return dict(hits[0])


def fsds_value(adsh: str, tag: str, segments: str) -> float:
    """The FSDS ``num`` value of one filing, tag and ``segments`` (the value oracle)."""
    found = {float(r["value"]) for r in FSDS["num"]
             if r["adsh"] == adsh and r["tag"] == tag and r["segments"] == segments}
    assert len(found) == 1, (adsh, tag, segments, found)
    return found.pop()


# The oracle points the tests quote as literals, checked against FSDS when the suite loads.
ORACLE_POINTS = [
    (INTC_OWN, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 6_266e6),
    (INTC_FY23, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 5_569e6),
    (INTC_FY24, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 8_207e6),
    (INTC_OWN, "OperatingIncomeLoss", "BusinessSegments=AllOtherSegments;", -5_614e6),
    (INTC_FY23, "OperatingIncomeLoss", "BusinessSegments=AllOtherSegments;", -5_977e6),
    (INTC_FY24, "OperatingIncomeLoss",
     "BusinessSegments=AllOtherSegments;ConsolidationItems=OperatingSegments;", 1_174e6),
    (INTC_OWN, "OperatingIncomeLoss", "", 2_334e6),
    (INTC_FY24, "OperatingIncomeLoss", "", 2_334e6),
    (INTC_OWN, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;", 31_708e6),
    (INTC_FY23, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;", 31_773e6),
    (INTC_FY24, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidationItems=OperatingSegments;", 31_773e6),
    (INTC_OWN, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
     "ProductOrService=Notebook;", 18_783e6),
    (INTC_FY23, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
     "ProductOrService=Notebook;", 18_781e6),
    (NVDA_OWN, "Revenues", "Geographical=OtherCountries;", 5_911e6),
    (NVDA_FY25, "Revenues", "Geographical=OtherCountries;", 3_623e6),
    (NVDA_FY25, "Revenues", "Geographical=SG;", 2_288e6),
    (NVDA_FY25, "Revenues", "Geographical=TW;", 6_986e6),
    (NVDA_OWN, "Revenues", "", 26_974e6),
    (NVDA_FY24, "Revenues",
     "BusinessSegments=Graphics;ConsolidationItems=OperatingSegments;", 11_906e6),
    (NVDA_FY25, "Revenues",
     "BusinessSegments=GraphicsSegment;ConsolidationItems=OperatingSegments;", 11_906e6),
]
for _adsh, _tag, _segments, _value in ORACLE_POINTS:
    assert fsds_value(_adsh, _tag, _segments) == pytest.approx(_value, abs=1e-6), _adsh


# ----------------------------------------------------------------------------- synthetic
SYNTH_ACCESSION = "0000050863-26-900001"   # not a real filing
SYNTH_FILED = "2026-01-23"


def synthetic_later_filing(rows: Sequence[Mapping[str, Any]], source: str, keep: Any,
                           change: Mapping[int, float] | None = None,
                           accession: str = SYNTH_ACCESSION, filed: str = SYNTH_FILED,
                           label: int = 2025) -> list[dict]:
    """A later canonical 10-K (synthetic) re-reporting some of ``source``'s rows.

    Args:
        rows: The store rows to copy from.
        source: The filing whose rows are copied.
        keep: A predicate on a row: which of ``source``'s rows the new filing reports.
        change: New values by the source row's ``fact_id``.
        accession, filed, label: The new filing's accession, filing date and fiscal-year
            label (its own DEI period end is a year after ``source``'s).

    Returns:
        The new filing's rows (fresh fact ids, not current-period rows).
    """
    change = dict(change or {})
    out = []
    for r in rows:
        if r["accession_no"] != source or not keep(r):
            continue
        c = dict(r)
        dpe = dt.date.fromisoformat(r["document_period_end"])
        c.update(accession_no=accession, filed_date=filed, fiscal_year=label,
                 document_period_end=dpe.replace(year=dpe.year + 1).isoformat(),
                 fact_id=9_000_000 + r["fact_id"] % 1_000_000, is_current_period=False)
        if r["fact_id"] in change:
            c["value"] = change[r["fact_id"]]
        out.append(c)
    return out


# ----------------------------------------------------------------------------- fake database
# Equality parameters the fake honours on mv_financial_facts and the multidim view (spec
# Notes, fake-database contract). Every other predicate on fiscal year, filing version or
# current period is ignored.
_EQ_PARAMS = ("cik", "ticker", "concept_family", "period_type", "duration_kind",
              "fiscal_period", "accession_no", "unit", "period_start", "period_end")
_FROM = re.compile(r"\bFROM\s+([a-z_]+)", re.IGNORECASE)
_VIEWS = ("mv_financial_facts_multidim", "mv_financial_facts", "mv_segment_matrix",
          "mv_geographic_facts", "concept_mapping", "entity_master", "filing_registry")
_IS_NULL = re.compile(r"\bsegment_axis\s+IS\s+NULL\b", re.IGNORECASE)
_IS_NOT_NULL = re.compile(r"\bsegment_axis\s+IS\s+NOT\s+NULL\b", re.IGNORECASE)
_USD = re.compile(r"\bunit\s*=\s*'USD'", re.IGNORECASE)
_SINGLE_DIM = re.compile(r"has_multiple_dimensions\s*(=\s*FALSE|IS\s+FALSE)"
                         r"|NOT\s+(\w+\.)?has_multiple_dimensions\b", re.IGNORECASE)
_FY_LE = re.compile(r"fiscal_year\s*<=\s*%\(fy\)s")
_FY_EQ = re.compile(r"fiscal_year\s*=\s*%\(fy\)s")


def _has(sql: str, name: str) -> bool:
    return f"%({name})s" in sql


def _like(value: str | None, pattern: str) -> bool:
    """SQL LIKE (backslash escapes)."""
    if value is None:
        return False
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.fullmatch("".join(out), value, re.DOTALL) is not None


def compact(label: str | None) -> str:
    """A label's compact form, as the lookup's and screens' SQL compute it."""
    s = (label or "").lower().replace("[member]", " ").replace("&", " and ")
    return re.sub(r"[^a-z0-9]", "", s)


def _equal_params(sql: str, p: Mapping[str, Any], r: Mapping[str, Any]) -> bool:
    for name in _EQ_PARAMS:
        if _has(sql, name) and p.get(name) is not None and r.get(name) != p[name]:
            return False
    for name, column in (("tickers", "ticker"), ("ciks", "cik")):
        if _has(sql, name) and p.get(name) is not None and r.get(column) not in set(p[name]):
            return False
    return True


def _segment_predicates(sql: str, p: Mapping[str, Any], r: Mapping[str, Any]) -> bool:
    """The member predicates, honoured as today's SQL applies them (not filing selection)."""
    if _IS_NULL.search(sql) and r.get("segment_axis") is not None:
        return False
    if _IS_NOT_NULL.search(sql) and r.get("segment_axis") is None:
        return False
    if _SINGLE_DIM.search(sql) and r.get("has_multiple_dimensions"):
        return False
    if _USD.search(sql) and r.get("unit") != "USD":
        return False
    if _has(sql, "axis_like") and not _like(r.get("segment_axis"), p["axis_like"]):
        return False
    if _has(sql, "member_qname") and r.get("segment_member") != p["member_qname"]:
        return False
    if _has(sql, "member_like") and not (
            _like(compact(r.get("segment_label")), p["member_like"])
            or _like((r.get("segment_label") or "").lower(), p["member_like"])):
        return False
    if _has(sql, "member_key") and p["member_key"] not in compact(r.get("segment_label")):
        return False
    if _has(sql, "segment_axes") and local(r.get("segment_axis")) not in set(p["segment_axes"]):
        return False
    return True


def _total(rows: Sequence[Mapping[str, Any]], r: Mapping[str, Any]) -> float | None:
    """The view's share denominator: the same filing's undimensioned total of the period."""
    vals = {t["value"] for t in rows
            if t["accession_no"] == r["accession_no"] and t["segment_axis"] is None
            and local(t["concept"]) == local(r["concept"]) and t["unit"] == r["unit"]
            and t["period_start"] == r["period_start"] and t["period_end"] == r["period_end"]
            and t["value"] is not None}
    return vals.pop() if len(vals) == 1 else None


def _view_row(rows: Sequence[Mapping[str, Any]], r: Mapping[str, Any]) -> dict:
    """A segment or geographic view row: the fact's columns plus the view's own."""
    row = flat_view([r])[0]
    tot = _total(rows, r)
    share = None if not tot or r["value"] is None else r["value"] / tot
    row.update(pct_of_axis_total=share, pct_of_total=share, segment_rank=None,
               # the breakdown's member query aliases these two
               member_label=r.get("segment_label"), member=r.get("segment_member"))
    member = local(r.get("segment_member"))
    is_country = str(r.get("segment_member") or "").startswith("country:")
    row.update(geo_member=r.get("segment_member"),
               geo_code=member if is_country else None,
               geo_name=COUNTRY_LABEL.get(member) if is_country else r.get("segment_label"),
               geo_type="country" if is_country else None)
    return row


class FakeStore:
    """The store's rows, the roster and the concept mapping for the fake connection."""

    def __init__(self, rows: Sequence[Mapping[str, Any]], roster: Sequence[str] = ("INTC",
                                                                                    "NVDA")):
        self.rows = [dict(r) for r in rows]
        self.roster = list(roster)

    def answer(self, sql: str, params: Any, as_dict: bool) -> list:
        p: Mapping[str, Any] = params if isinstance(params, Mapping) else {}
        if "to_regclass" in sql:
            return [{"present": False} if as_dict else (False,)]
        names = [n.lower() for n in _FROM.findall(sql)]
        view = next((n for n in names if n in _VIEWS), None)
        if view == "concept_mapping":
            family = params[0] if isinstance(params, (list, tuple)) else p.get("concept_family")
            qnames = sorted({r["concept"] for r in self.rows if r["concept_family"] == family})
            out = [(q, 1, None, None, None) for q in qnames]
            if as_dict:
                keys = ("qname", "preference_rank", "concept_role", "substitution_code",
                        "substitution_note")
                return [dict(zip(keys, t, strict=True)) for t in out]
            return out
        if view == "entity_master":
            rows = [{"cik": CIK[t], "ticker": t} for t in sorted(self.roster)]
        elif view == "filing_registry":
            rows = sorted(({"accession_no": a, "cik": r["cik"], "ticker": r["ticker"],
                            "fiscal_year": r["fiscal_year"],
                            "fiscal_period": r["fiscal_period"], "form_type": r["form_type"],
                            "filed_date": r["filed_date"], "is_amendment": r["is_amendment"],
                            "superseded_by": None}
                           for a, r in {r["accession_no"]: r for r in self.rows}.items()
                           if "cik" not in p or r["cik"] == p["cik"]),
                          key=lambda x: (x["filed_date"], x["accession_no"]))
        elif view == "mv_financial_facts_multidim":
            rows = [r for r in multidim_view(self.rows) if _equal_params(sql, p, r)]
            if _has(sql, "concept_qnames"):
                rows = [r for r in rows if r["concept"] in set(p["concept_qnames"])]
        elif view == "mv_segment_matrix" or view == "mv_geographic_facts":
            rows = []
            for r in self.rows:
                if r["segment_axis"] is None or not r["is_canonical_filing"] \
                        or not r["is_current_period"] or r["period_type"] != "duration":
                    continue
                if view == "mv_geographic_facts" and local(r["segment_axis"]) not in GEO_AXES:
                    continue
                if _FY_LE.search(sql) and not r["fiscal_year"] <= p["fy"]:
                    continue
                if _FY_EQ.search(sql) and r["fiscal_year"] != p["fy"]:
                    continue
                if not (_equal_params(sql, p, r) and _segment_predicates(sql, p, r)):
                    continue
                vr = _view_row(self.rows, r)
                if _has(sql, "geo_code") and vr["geo_code"] != p["geo_code"]:
                    continue
                rows.append(vr)
        elif view == "mv_financial_facts":
            rows = [r for r in flat_view(self.rows)
                    if _equal_params(sql, p, r) and _segment_predicates(sql, p, r)]
        else:
            rows = []
        rows = [dict(r) for r in rows]
        return rows if as_dict else [tuple(r.values()) for r in rows]


class FakeCursor:
    """A cursor over the fake store."""

    def __init__(self, store: FakeStore, as_dict: bool):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._rows = self._store.answer(sql, params, self._as_dict)

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def mogrify(self, sql: str, params: Any = None) -> bytes:
        return sql.encode("utf-8")

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeConn:
    """A psycopg2 connection over a ``FakeStore``."""

    def __init__(self, store: FakeStore):
        self._store = store

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self) -> dict:
        return {"dbname": "fake_dimensional_s1_db"}

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:
        pass


def run_lookup(rows: Sequence[Mapping[str, Any]], ticker: str, fy: int, family: str,
               dimensions: Mapping[str, str], **step: Any):
    """``XbrlFactstoreLookup.lookup`` of one dimensional step over the fake store."""
    store = FakeStore(rows)
    full = {"id": "d1", "op": "lookup", "cik": CIK[ticker], "ticker": ticker, "fy": fy,
            "concept_family": family, "dimensions": dict(dimensions), **step}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreLookup(db_conn_params={}).lookup(full)


def run_screen(rows: Sequence[Mapping[str, Any]], fy: int, family: str,
               dimension: Mapping[str, str], metric_kind: str = "value"):
    """A member ``XbrlFactstoreScreen.screen`` (FY, every corpus filer) over the fake store."""
    store = FakeStore(rows)
    step = {"id": "s1", "op": "screen", "fy": fy, "concept_family": family,
            "dimension": dict(dimension), "metric_kind": metric_kind, "order": "desc",
            "top_n": None}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


def run_breakdown(rows: Sequence[Mapping[str, Any]], ticker: str, fy: int, family: str,
                  axis: str, **step: Any):
    """``XbrlFactstoreBreakdown.breakdown`` of one filer-year over the fake store."""
    store = FakeStore(rows)
    full = {"id": "b1", "op": "breakdown", "cik": CIK[ticker], "fy": fy,
            "concept_family": family, "axis": axis, **step}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreBreakdown(db_conn_params={}).breakdown(full)


def ranked(result: Any, ticker: str) -> dict | None:
    """A screen result's row for one filer, or None."""
    hits = [r for r in result.rows if r.get("ticker") == ticker]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis).

    Built from this module's globals: every spec directory has a module named ``conftest``,
    so ``sys.modules`` cannot name this one.
    """
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
