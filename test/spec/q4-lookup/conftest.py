"""Fixtures for q4-lookup (docs/specs/q4-lookup.md).

`select_q4` is pure: nothing is faked. `fixtures/store_rows.json` holds 44 stored
`mv_financial_facts` rows of MSFT's FY2026 10-K and Q3 10-Q (read-only xbrl_db,
2026-11-11); `fixtures/fsds_num.json` the FSDS `num` lines the expected values are read
from (each source with URL and SHA-256). `synth` builds corpus-shaped rows for the cases
the corpus does not hold (a mis-tagged quarter, restatements), with the same columns.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import types
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

MSFT_10K = "0001193125-26-323660"
MSFT_10Q = "0001193125-26-191507"
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
DIVIDENDS = "DividendsCommonStockCash"
DPS = "CommonStockDividendsPerShareDeclared"

KEYS = {"status", "value", "unit", "period_start", "period_end", "days", "decimals",
        "fact_ids", "accessions", "basis", "note", "reason"}


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _decimalize(row: dict) -> dict:
    out = dict(row)
    if out.get("value") is not None:
        out["value"] = Decimal(out["value"])
    return out


_STORE = [_decimalize(r) for r in _load("store_rows.json")["rows"]]
_FSDS = _load("fsds_num.json")


def local(concept: str | None) -> str | None:
    """A QName's local name."""
    return None if concept is None else concept.rsplit(":", 1)[-1]


def store(*concepts: str, accessions: tuple[str, ...] = (MSFT_10K, MSFT_10Q)) -> list[dict]:
    """Fresh copies of the stored rows of the given concepts (local names) and filings."""
    return [copy.deepcopy(r) for r in _STORE
            if local(r["concept"]) in concepts and r["accession_no"] in accessions]


def fsds_value(adsh: str, tag: str, ddate: str, qtrs: int) -> Decimal:
    """The FSDS `num` value of one filing, tag, date and quarter count (no segment)."""
    hits = []
    for entry in _FSDS["lines"]:
        f = entry["line"].split("\t")
        if f[0] == adsh and f[1] == tag and f[3] == ddate and f[4] == str(qtrs) and f[6] == "":
            hits.append(Decimal(f[8]))
    assert len(hits) == 1, (adsh, tag, ddate, qtrs, hits)
    return hits[0]


def synth(fact_id: int, label: int, fp: str, start: dt.date, days: int, value, accession: str,
          filed: dt.date, *, concept: str = "http://fasb.org/us-gaap/2024:Revenues",
          unit: str = "USD", decimals: int | None = -6, own: bool = True,
          segment_axis: str | None = None) -> dict:
    """A corpus-shaped stored duration row (stored end = start + days).

    ``own`` puts it in its filing's own column (DEI end = the stored end - 1 day); else the
    filing's DEI end is a year later (a comparative).
    """
    end = start + dt.timedelta(days=days)
    dpe = end - dt.timedelta(days=1) if own else end + dt.timedelta(days=364)
    return {
        "fact_id": fact_id, "ticker": "SYN", "cik": "0000000001", "fiscal_year": label,
        "fiscal_period": fp, "duration_kind": None, "period_type": "duration",
        "concept": concept, "concept_family": "Revenue", "concept_pref_rank": 1,
        "concept_role": "primary", "presented": True, "segment_axis": segment_axis,
        "segment_member": None if segment_axis is None else "syn:OtherMember",
        "value": None if value is None else Decimal(value), "unit": unit,
        "decimals": decimals, "period_start": start.isoformat(),
        "period_end": end.isoformat(), "accession_no": accession,
        "filed_date": filed.isoformat(), "is_amendment": False, "is_canonical_filing": True,
        "document_period_end": dpe.isoformat(),
    }


def assert_refused(out: dict) -> None:
    """R7: a refusal has value None, a non-empty reason and every other key None."""
    assert set(out) == KEYS
    assert out["status"] == "refused"
    assert isinstance(out["reason"], str) and out["reason"].strip()
    for k in KEYS - {"status", "reason"}:
        assert out[k] is None, (k, out[k])


@pytest.fixture(scope="session")
def fx():
    """Everything the tests share, as one namespace (session-scoped: safe with hypothesis)."""
    return types.SimpleNamespace(
        MSFT_10K=MSFT_10K, MSFT_10Q=MSFT_10Q, REVENUE=REVENUE, DIVIDENDS=DIVIDENDS, DPS=DPS,
        KEYS=KEYS, store=store, fsds_value=fsds_value, synth=synth, local=local,
        assert_refused=assert_refused,
    )
