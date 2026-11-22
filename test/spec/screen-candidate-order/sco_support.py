"""Fixture readers for the screen-candidate-order spec tests (imported as ``sco_support``).

The fixtures are byte copies from two SEC sources, independent of the fact store:

- ``fixtures/fsds/<quarter>/{sub,num,pre}.tsv``: lines of SEC's Financial Statement Data Sets
  (the quarterly zips named in ``fixtures/provenance.json``) for the fixture filings and the
  fixture tags. ``pre.tsv`` says which line items a filing presents on its statements.
- ``fixtures/companyfacts_excerpt.json``: entries of SEC's companyfacts API for the same
  filings and tags, notes included (FSDS holds statement facts only).

``store_rows`` turns one filing into the candidate rows a fact-store query returns after the
change: every fixture tag that is a member of the family for the filer (by ``family_member``,
the committed mapping), at the filing's own period, with ``presented`` read from FSDS. Nothing
in ``src`` is faked.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from src.retriever.xbrl.family_resolution import family_member

FIXTURES = Path(__file__).parent / "fixtures"
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
COMPANYFACTS = json.loads((FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8"))

# SEC EDGAR CIKs (10-digit) of the fixture filers.
CIK = {
    "AMD": "0000002488", "AMZN": "0001018724", "AVGO": "0001730168", "COST": "0000909832",
    "CRM": "0001108524", "GOOGL": "0001652044", "HD": "0000354950", "INTC": "0000050863",
    "META": "0001326801", "MU": "0000723125", "NVDA": "0001045810", "ORCL": "0001341439",
    "PG": "0000080424", "QCOM": "0000804328", "TSLA": "0001318605", "TXN": "0000097476",
    "UPS": "0001090727", "VST": "0001692819",
}
NO_ENTRIES_CIK = "0000000001"  # a CIK with no cik-scoped or reviewed entry
G = "http://fasb.org/us-gaap/2025:"  # the store form of a us-gaap concept

# The primary statements of FSDS ``pre.stmt`` (balance sheet, income statement, cash flow,
# comprehensive income, equity); a parenthetical (``inpth`` 1) is not a line item.
_STATEMENTS = {"BS", "IS", "CF", "CI", "EQ"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_bytes() -> None:
    for rel, digest in PROVENANCE["files"].items():
        assert _sha(FIXTURES / rel) == digest, f"fixture {rel} changed"


_check_bytes()


def _read(quarter: str, name: str) -> list[dict[str, str]]:
    path = FIXTURES / "fsds" / quarter / f"{name}.tsv"
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


FILINGS: dict[tuple[str, int], dict[str, Any]] = {}
for _f in PROVENANCE["filings"]:
    FILINGS[(_f["ticker"], _f["fiscal_year_label"])] = _f
_PRESENTED: dict[str, set[str]] = {}
for _q in sorted({f["quarter"] for f in PROVENANCE["filings"]}):
    for _r in _read(_q, "pre"):
        if _r["stmt"] in _STATEMENTS and _r["inpth"] == "0":
            _PRESENTED.setdefault(_r["adsh"], set()).add(_r["tag"])


def accession(ticker: str, fy: int) -> str:
    """The fixture 10-K labelled ``fy`` for ``ticker``."""
    return FILINGS[(ticker, fy)]["accession"]


def presented(ticker: str, fy: int, tag: str) -> bool:
    """Whether the filing presents ``tag`` as a line item of a primary statement (FSDS)."""
    return tag in _PRESENTED.get(accession(ticker, fy), set())


def tags_by_year(ticker: str) -> dict[str, set[int]]:
    """us-gaap tag -> the fixture fiscal-year labels of the 10-Ks that report it (companyfacts).

    companyfacts holds only undimensioned facts, so this is the facts-count pass's input:
    the fiscal years in which the filer reports the tag undimensioned in an annual filing.
    """
    label = {f["accession"]: f["fiscal_year_label"] for f in PROVENANCE["filings"]
             if f["ticker"] == ticker}
    facts = COMPANYFACTS["facts_by_cik"][str(int(CIK[ticker]))]["us-gaap"]
    out: dict[str, set[int]] = {}
    for tag, units in facts.items():
        for entries in units.values():
            for e in entries:
                if e["accn"] in label:
                    out.setdefault(tag, set()).add(label[e["accn"]])
    return out


def _own_period(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries of the filing's own column: the latest end; a duration of about a year."""
    end = max(e["end"] for e in entries)
    own = [e for e in entries if e["end"] == end]
    durations = [e for e in own if e.get("start")]
    if durations:
        own = [e for e in durations
               if 350 <= (dt.date.fromisoformat(e["end"])
                          - dt.date.fromisoformat(e["start"])).days <= 380]
    return own


def store_rows(ticker: str, fy: int, family: str, cik: str | None = None) -> list[dict]:
    """The filing's candidate rows of ``family`` after the change, one per member tag.

    Args:
        ticker: A fixture filer.
        fy: The fixture fiscal-year label of its 10-K.
        family: The concept family.
        cik: The CIK whose mapping entries apply (default: the filer's own).

    Returns:
        One row per fixture tag that is a member of ``family`` for the filer and that the
        filing reports in USD at its own period, with the mapping's rank and role, FSDS's
        ``presented`` and companyfacts' value. Exact facts (``decimals`` None).
    """
    cik = cik or CIK[ticker]
    adsh = accession(ticker, fy)
    facts = COMPANYFACTS["facts_by_cik"][str(int(CIK[ticker]))]["us-gaap"]
    rows: list[dict] = []
    for n, tag in enumerate(sorted(facts)):
        entries = [e for e in facts[tag].get("USD", []) if e["accn"] == adsh]
        if not entries:
            continue
        member = family_member(family, cik, G + tag)
        if member is None:
            continue
        own = _own_period(entries)
        values = {e["val"] for e in own}
        assert len(values) == 1, f"{ticker} {fy} {tag}: {sorted(values)}"
        rows.append({
            "ticker": ticker, "cik": CIK[ticker], "fiscal_year": fy,
            "period_end": own[0]["end"], "period_start": own[0].get("start"),
            "concept": G + tag, "concept_family": family,
            "concept_pref_rank": member["rank"], "concept_role": member["role"],
            "presented": presented(ticker, fy, tag), "value": float(own[0]["val"]),
            "decimals": None, "fact_id": 1000 + n, "unit": "USD", "accession_no": adsh,
        })
    return rows
