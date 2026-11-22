"""Fixtures for the set-b-dimensional-s1 spec tests (docs/specs/set-b-dimensional-s1.md).

Real excerpts, committed under ``fixtures/`` (provenance inside each file and in the spec's
Notes; the SHA-256 of each file is checked below):

- ``fsds_segments.json``: a byte copy of ``test/spec/dimensional-s1/fixtures/`` — SEC Financial
  Statement Data Sets ``sub``/``num`` rows, segments kept, of Intel's FY2022-FY2024 10-Ks at the
  FY2022 period and NVIDIA's FY2023-FY2025 10-Ks at the FY2023 period;
- ``fsds_nvda_recast.json``: FSDS ``sub``/``num`` rows (``Revenues``, annual, USD, segments kept)
  of NVIDIA's FY2024, FY2025 and FY2026 10-Ks at the FY2024 and FY2025 periods (built for this
  spec from the 2024q1, 2025q1 and 2026q1 archives);
- ``dei.json``: a byte copy of ``test/spec/set-b-restatement/fixtures/dei.json`` — each corpus
  filing's DEI from its iXBRL instance.

Store rows (``mv_financial_facts`` shape, plus the ``mv_financial_facts_multidim``
``dimensions`` / ``dimension_labels`` of a multi-dimension fact) are built from the ``num`` rows
as the spec's Notes describe. The database is the only I/O boundary: ``FakeConn`` follows the
spec's fake-database contract. Nothing reads the network, the current date or anything outside
this directory. No set B, H-X or held-out content is used.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]
FIXTURE_SHA256 = {
    "fsds_segments.json": "5652a018bf3f4c4987e1b1b396a76be9f2aea80b4b0e40a7d9607da096d519dd",
    "fsds_nvda_recast.json": "9a24b4045c3efcff2a0ef826e6aec4aea576ef7bf685cd9db79462c200f88ca5",
    "dei.json": "2e261b57dd0843f2b4999df274ad4691bfd2d495972721f6c91a6552a8436a1f",
}
for _name, _digest in FIXTURE_SHA256.items():
    assert hashlib.sha256((FIXTURES / _name).read_bytes()).hexdigest() == _digest, _name

DEI: dict[str, dict] = json.loads((FIXTURES / "dei.json").read_text("utf-8"))
NUM: list[dict] = (json.loads((FIXTURES / "fsds_segments.json").read_text("utf-8"))["num"]
                   + json.loads((FIXTURES / "fsds_nvda_recast.json").read_text("utf-8"))["num"])

ONE_DAY = dt.timedelta(days=1)

# The filings, by fiscal-year label.
INTC_22, INTC_23, INTC_24 = (
    "0000050863-23-000006", "0000050863-24-000010", "0000050863-25-000009")
NVDA_23, NVDA_24, NVDA_25, NVDA_26 = (
    "0001045810-23-000017", "0001045810-24-000029", "0001045810-25-000023",
    "0001045810-26-000021")
TICKER = {"0000050863": "INTC", "0001045810": "NVDA"}
FILINGS = {"INTC": (INTC_22, INTC_23, INTC_24), "NVDA": (NVDA_23, NVDA_24, NVDA_25, NVDA_26)}
OWN = {("INTC", 2022): INTC_22, ("NVDA", 2023): NVDA_23, ("NVDA", 2024): NVDA_24,
       ("NVDA", 2025): NVDA_25}


def _day(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


# The annual period of each FSDS ``ddate`` (SEC rounds it to the month end). The end is the own
# filing's DEI DocumentPeriodEndDate. The start of a NVIDIA year is the day after the prior own
# filing's DocumentPeriodEndDate (its 52/53-week years are contiguous); Intel FY2022 and NVIDIA
# FY2023 starts are companyfacts' (restatement-s1 fixtures, as dimensional-s1 uses them).
PERIOD = {
    ("INTC", "20221231"): ("2021-12-26", DEI[INTC_22]["document_period_end"]),
    ("NVDA", "20230131"): ("2022-01-31", DEI[NVDA_23]["document_period_end"]),
    ("NVDA", "20240131"): (
        (_day(DEI[NVDA_23]["document_period_end"]) + ONE_DAY).isoformat(),
        DEI[NVDA_24]["document_period_end"]),
    ("NVDA", "20250131"): (
        (_day(DEI[NVDA_24]["document_period_end"]) + ONE_DAY).isoformat(),
        DEI[NVDA_25]["document_period_end"]),
}
assert PERIOD[("INTC", "20221231")][1] == "2022-12-31"
assert PERIOD[("NVDA", "20240131")] == ("2023-01-30", "2024-01-28")
assert PERIOD[("NVDA", "20250131")] == ("2024-01-29", "2025-01-26")
YEAR_OF = {("INTC", "20221231"): 2022, ("NVDA", "20230131"): 2023,
           ("NVDA", "20240131"): 2024, ("NVDA", "20250131"): 2025}
DDATE = {v: k[1] for k, v in YEAR_OF.items() if k[0] == "NVDA"} | {2022: "20221231"}

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
COUNTRY_LABEL = {"TW": "Taiwan", "US": "United States", "SG": "Singapore"}


def local(qname: Any) -> str:
    """The local name of a QName (after the last ':' or '}')."""
    return re.split(r"[:}]", str(qname))[-1]


def next_day(value: str) -> str:
    """The store's next-midnight end of an inclusive SEC date."""
    return (_day(value) + ONE_DAY).isoformat()


def member_qname(ticker: str, axis: str, name: str) -> str:
    """The member QName FSDS's ``Axis=Name`` stands for (country codes in ``country:``)."""
    if axis == "Geographical" and re.fullmatch(r"[A-Z]{2}", name):
        return f"country:{name}"
    if name in ("OperatingSegments", "CorporateNonSegment", "IntersegmentElimination"):
        return f"us-gaap:{name}Member"
    return f"{ticker.lower()}:{name}Member"


def member_label(axis: str, name: str) -> str:
    """A synthetic label: the name's words plus ``[Member]`` (countries by name).

    Labels are not oracle values: they only let a key's member hint find the member.
    """
    if axis == "Geographical" and name in COUNTRY_LABEL:
        return f"{COUNTRY_LABEL[name]} [Member]"
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
    ordered = sorted(NUM, key=lambda r: (r["adsh"], r["ddate"], r["tag"], r["segments"]))
    for i, n in enumerate(ordered):
        accn = n["adsh"]
        d = DEI[accn]
        ticker = TICKER[d["cik"]]
        start, end = PERIOD[(ticker, n["ddate"])]
        dims = parse_segments(n["segments"])
        row = {
            "fact_id": 5_000_000 + i, "cik": d["cik"], "ticker": ticker, "accession_no": accn,
            "fiscal_year": int(d["fiscal_year_focus"]),
            "fiscal_period": d["fiscal_period_focus"], "filed_date": d["filed"],
            "is_amendment": d["form"].endswith("/A"), "is_canonical_filing": True,
            "document_period_end": d["document_period_end"],
            "concept": f"{d['us_gaap_namespace']}:{n['tag']}",
            "concept_family": FAMILY[n["tag"]], "concept_pref_rank": 1,
            "value": float(n["value"]), "unit": "USD",
            "period_type": "duration", "period_start": start, "period_end": next_day(end),
            "duration_kind": "annual",
            "is_current_period": end == d["document_period_end"],
            "segment_axis": None, "segment_member": None, "segment_label": None,
            "has_multiple_dimensions": False, "dimensions": None, "dimension_labels": None,
        }
        if dims:
            # The flat view files a fact under its first dimension (FSDS's first pair) and
            # flags a multi-dimension one; the multidim view adds its full dimension set.
            axis, name = dims[0]
            row.update(segment_axis=AXIS_QNAME[axis],
                       segment_member=member_qname(ticker, axis, name),
                       segment_label=member_label(axis, name),
                       has_multiple_dimensions=len(dims) > 1)
            if len(dims) > 1:
                row["dimensions"] = {AXIS_QNAME[a]: member_qname(ticker, a, m)
                                     for a, m in dims}
                row["dimension_labels"] = {AXIS_QNAME[a]: member_label(a, m) for a, m in dims}
        rows.append(row)
    return rows


_ROWS = _build_rows()


def store_rows(ticker: str | None = None,
               accessions: Iterable[str] | None = None) -> list[dict]:
    """Fresh copies of the store's rows, optionally of one filer and some filings."""
    keep = None if accessions is None else set(accessions)
    return [dict(r) for r in _ROWS
            if (ticker is None or r["ticker"] == ticker)
            and (keep is None or r["accession_no"] in keep)]


def dims_of(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """A row's full dimension set by local name, from the fixture (oracle side)."""
    if row.get("dimensions"):
        return tuple(sorted((local(a), local(m)) for a, m in row["dimensions"].items()))
    if row.get("segment_axis") is None:
        return ()
    return ((local(row["segment_axis"]), local(row["segment_member"])),)


def find(accession: str, tag: str, segments: str, fy: int) -> dict:
    """The one store row of a filing, tag, FSDS ``segments`` text and fiscal-year period."""
    ticker = TICKER[accession[:10]]
    start, end = PERIOD[(ticker, DDATE[fy])]
    want = tuple(sorted((local(AXIS_QNAME[a]), local(member_qname(ticker, a, m)))
                        for a, m in parse_segments(segments)))
    hits = [r for r in _ROWS if r["accession_no"] == accession and local(r["concept"]) == tag
            and dims_of(r) == want and r["period_start"] == start
            and r["period_end"] == next_day(end)]
    assert len(hits) == 1, (accession, tag, segments, fy, len(hits))
    return dict(hits[0])


def fsds_value(adsh: str, tag: str, segments: str, fy: int) -> float:
    """The FSDS ``num`` value of one filing, tag, ``segments`` and year (the value oracle)."""
    found = {float(r["value"]) for r in NUM
             if r["adsh"] == adsh and r["tag"] == tag and r["segments"] == segments
             and r["ddate"] == DDATE[fy]}
    assert len(found) == 1, (adsh, tag, segments, fy, found)
    return found.pop()


# The oracle points the tests quote as literals, checked against FSDS when the suite loads.
GEO = "Geographical="
ORACLE_POINTS = [
    # NVIDIA FY2025 geography: the FY2025 10-K, and the FY2026 10-K's recast (no Singapore).
    (NVDA_25, "Revenues", "Geographical=US;", 2025, 61_257e6),
    (NVDA_25, "Revenues", "Geographical=TW;", 2025, 20_573e6),
    (NVDA_25, "Revenues", "Geographical=SG;", 2025, 23_684e6),
    (NVDA_25, "Revenues", "Geographical=ChinaIncludingHongKong;", 2025, 17_108e6),
    (NVDA_25, "Revenues", "Geographical=OtherCountries;", 2025, 7_875e6),
    (NVDA_26, "Revenues", "Geographical=US;", 2025, 77_482e6),
    (NVDA_26, "Revenues", "Geographical=TW;", 2025, 23_600e6),
    (NVDA_26, "Revenues", "Geographical=ChinaIncludingHongKong;", 2025, 25_048e6),
    (NVDA_26, "Revenues", "Geographical=OtherCountries;", 2025, 4_367e6),
    (NVDA_26, "Revenues", "", 2025, 130_497e6),
    # NVIDIA FY2025 product split, the same in both 10-Ks.
    (NVDA_26, "Revenues", "ProductOrService=DataCenter;", 2025, 115_186e6),
    (NVDA_26, "Revenues", "ProductOrService=Compute;", 2025, 102_196e6),
    # NVIDIA FY2024: the FY2024 10-K's product split, and the FY2026 10-K's recast.
    (NVDA_24, "Revenues", "ProductOrService=DataCenter;", 2024, 47_525e6),
    (NVDA_26, "Revenues", "ProductOrService=Compute;", 2024, 38_950e6),
    (NVDA_26, "Revenues", "ProductOrService=Networking;", 2024, 8_575e6),
    (NVDA_26, "Revenues", "ProductOrService=Gaming;", 2024, 10_447e6),
    (NVDA_26, "Revenues", "ProductOrService=ProfessionalVisualization;", 2024, 1_553e6),
    (NVDA_26, "Revenues", "ProductOrService=Automotive;", 2024, 1_091e6),
    (NVDA_26, "Revenues", "ProductOrService=OEMAndOther;", 2024, 306e6),
    (NVDA_26, "Revenues", "", 2024, 60_922e6),
    (NVDA_24, "Revenues", "Geographical=OtherCountries;", 2024, 10_245e6),
    (NVDA_25, "Revenues", "Geographical=SG;", 2024, 6_831e6),
    (NVDA_26, "Revenues", "Geographical=TW;", 2024, 14_912e6),
    (NVDA_24, "Revenues",
     "BusinessSegments=ComputeAndNetworking;ConsolidationItems=OperatingSegments;", 2024,
     47_405e6),
    # NVIDIA FY2023 geography (the FY2025 10-K splits Singapore out) and the renamed segment.
    (NVDA_25, "Revenues", "Geographical=SG;", 2023, 2_288e6),
    (NVDA_25, "Revenues", "Geographical=OtherCountries;", 2023, 3_623e6),
    (NVDA_23, "Revenues", "Geographical=OtherCountries;", 2023, 5_911e6),
    (NVDA_24, "Revenues",
     "BusinessSegments=Graphics;ConsolidationItems=OperatingSegments;", 2023, 11_906e6),
    # Intel FY2022: segment operating income in three 10-Ks, and a three-dimension cell.
    (INTC_24, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 2022, 8_207e6),
    (INTC_23, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 2022, 5_569e6),
    (INTC_22, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;", 2022, 6_266e6),
    (INTC_23, "OperatingIncomeLoss", "BusinessSegments=AllOtherSegments;", 2022, -5_977e6),
    (INTC_24, "OperatingIncomeLoss", "BusinessSegments=DatacenterAndAI;", 2022, 4_198e6),
    (INTC_24, "OperatingIncomeLoss", "BusinessSegments=NetworkAndEdge;", 2022, 1_532e6),
    (INTC_23, "RevenueFromContractWithCustomerExcludingAssessedTax",
     "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
     "ProductOrService=Notebook;", 2022, 18_781e6),
]
for _adsh, _tag, _segments, _fy, _value in ORACLE_POINTS:
    assert fsds_value(_adsh, _tag, _segments, _fy) == pytest.approx(_value, abs=1e-6), _adsh


def dimensional(cands: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The dimensional candidates."""
    return [c for c in cands if c["dimensional"]]


def by_id(rows: Iterable[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    return {int(r["fact_id"]): r for r in rows}


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
        # The multidim view's dimension set comes back only when the query names its column.
        names_dims = re.search(r"\bdimensions\b", sql) is not None
        rows = []
        for r in self._conn.rows:
            if r["ticker"] != ticker:
                continue
            row = dict(r)
            if not names_dims:
                row.pop("dimensions", None)
            rows.append(row)
        self._rows = rows

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


def other_filer_rows(rows: Iterable[Mapping[str, Any]], ticker: str) -> list[dict]:
    """Copies of ``rows`` filed under another (made-up) filer, with new fact ids."""
    out = []
    for r in rows:
        row = dict(r)
        row.update(ticker=ticker, cik="0000000001", fact_id=9_000_000 + int(r["fact_id"]))
        out.append(row)
    return out


def load_set_b_cli() -> Any:
    """``scripts/eval/set_b.py`` as a module (its ``LabelRecordingStore``)."""
    spec = importlib.util.spec_from_file_location("set_b_cli_bds1",
                                                  REPO / "scripts" / "eval" / "set_b.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis).

    Built from this module's globals: every spec directory has a module named ``conftest``, so
    ``sys.modules`` cannot name this one.
    """
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
