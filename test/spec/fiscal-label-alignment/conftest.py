"""Filings, manifests and companyfacts payloads for the fiscal-label-alignment spec tests.

Spec: docs/specs/fiscal-label-alignment.md. CRM, HD and WMT rows are the filings' own period
ends and DEI tags, with values from their raw iXBRL. Every other filer is synthetic, with a
made-up CIK. The fakes sit at the SEC and fact-store seams only. Everything is exposed through
one session-scoped ``kit`` fixture so hypothesis tests can use it.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.suites.v2_suite import FactStoreSource
from src.ingest.sec_edgar.ingest.layer0_populator import ManifestRecord

CRM_CIK = "0001108524"
HD_CIK = "0000354950"
WMT_CIK = "0000104169"
SYN_CIK = "0000900201"  # synthetic December filer
JAN_CIK = "0000900202"  # synthetic January filer with 10-Qs

MN = 1_000_000

C23 = "0001108524-23-000011"
C24 = "0001108524-24-000005"
C25 = "0001108524-25-000006"
C26 = "0001108524-26-000060"

H24 = "0000354950-24-000062"
H25 = "0000354950-25-000085"
H26 = "0001628280-26-019436"

W23 = "0000104169-23-000020"
W24 = "0000104169-24-000056"
W25 = "0000104169-25-000021"
W26 = "0000104169-26-000055"


def filing(accession: str, end: date, dei: int | None, form: str = "10-K",
           filed: date | None = None) -> dict:
    """One ``assign_fiscal_labels`` row; filed on December 31 of its year unless given."""
    return {"accession_no": accession, "form_type": form, "period_end": end,
            "filed_date": filed or date(end.year, 12, 31), "dei_fiscal_year": dei}


CRM = [
    filing(C23, date(2023, 1, 31), 2023, filed=date(2023, 3, 8)),
    filing(C24, date(2024, 1, 31), 2024, filed=date(2024, 3, 6)),
    filing(C25, date(2025, 1, 31), 2025, filed=date(2025, 3, 5)),
    filing(C26, date(2026, 1, 31), 2025, filed=date(2026, 3, 2)),
]
HD = [
    filing(H24, date(2024, 1, 28), 2023, filed=date(2024, 3, 13)),
    filing(H25, date(2025, 2, 2), 2024, filed=date(2025, 3, 21)),
    filing(H26, date(2026, 2, 1), 2025, filed=date(2026, 3, 18)),
]
WMT = [
    filing(W23, date(2023, 1, 31), 2023, filed=date(2023, 3, 17)),
    filing(W24, date(2024, 1, 31), 2024, filed=date(2024, 3, 15)),
    filing(W25, date(2025, 1, 31), 2025, filed=date(2025, 3, 14)),
    filing(W26, date(2026, 1, 31), 2026, filed=date(2026, 3, 13)),
]


def run_of(deis: list[int | None], first_end_year: int = 2015) -> list[dict]:
    """Consecutive January-31 years, one filing each, with the given DEI tags."""
    return [filing(f"0000900300-{i:02d}-000001", date(first_end_year + i, 1, 31), d)
            for i, d in enumerate(deis)]


def labels_in_order(labels: dict[str, int], filings: list[dict]) -> list[int]:
    """The labels of ``filings``, in the given order."""
    return [labels[f["accession_no"]] for f in filings]


def manifest(row: dict, cik: str, ticker: str, manifest_year: int | None = None,
             quarter: int | None = None) -> ManifestRecord:
    """The parsed manifest of one filing row; its year is the period end's unless given."""
    form = row["form_type"]
    return ManifestRecord(
        ticker=ticker, cik=cik, accession_no=row["accession_no"], form_type=form,
        filing_type=form.removesuffix("/A"), is_amendment=form.endswith("/A"),
        fiscal_year=row["period_end"].year if manifest_year is None else manifest_year,
        quarter=quarter, filed_date=row["filed_date"], period_start=date(2020, 1, 1),
        period_end=row["period_end"], fye_month=1, fye_day=31,
    )


def dei_of(rows: list[dict]) -> dict[str, int | None]:
    """Accession → DEI, for ``apply_fiscal_labels``."""
    return {r["accession_no"]: r["dei_fiscal_year"] for r in rows}


def entry(period: tuple[str | None, str], val: float, accn: str, fy: int | None, filed: str,
          form: str = "10-K", fp: str = "FY") -> dict:
    """One companyfacts entry; a period is ``(start, end)``, with ``None`` for an instant."""
    start, end = period
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start is not None:
        out["start"] = start
    return out


Y23 = ("2022-02-01", "2023-01-31")
Y24 = ("2023-02-01", "2024-01-31")
Y25 = ("2024-02-01", "2025-01-31")
Y26 = ("2025-02-01", "2026-01-31")
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
ASSETS = "us-gaap:Assets"


def crm_companyfacts() -> dict:
    """Salesforce's companyfacts shape: both of the last two 10-Ks have ``fy`` 2025."""
    f25, f26, f24, f23 = "2025-03-05", "2026-03-02", "2024-03-06", "2023-03-08"
    revenue = [
        entry(Y23, 31_352 * MN, C23, 2023, f23),
        entry(Y23, 31_352 * MN, C24, 2024, f24), entry(Y24, 34_857 * MN, C24, 2024, f24),
        entry(Y23, 31_352 * MN, C25, 2025, f25), entry(Y24, 34_857 * MN, C25, 2025, f25),
        entry(Y25, 37_895 * MN, C25, 2025, f25),
        entry(Y24, 34_857 * MN, C26, 2025, f26), entry(Y25, 37_895 * MN, C26, 2025, f26),
        entry(Y26, 41_525 * MN, C26, 2025, f26),
        # A first-quarter 10-Q of fiscal 2026: not an annual filing. Value not asserted.
        entry(("2025-02-01", "2025-04-30"), 9_000 * MN, "0001108524-25-000099", 2026, "2025-06-05",
              form="10-Q", fp="Q1"),
    ]
    assets = [
        entry((None, "2024-01-31"), 99_823 * MN, C25, 2025, f25),
        entry((None, "2025-01-31"), 102_928 * MN, C25, 2025, f25),
        entry((None, "2025-01-31"), 102_928 * MN, C26, 2025, f26),
        entry((None, "2026-01-31"), 112_305 * MN, C26, 2025, f26),
    ]
    shares = [  # cover-page instants fall after the year end; they say nothing about the period
        entry((None, "2025-02-24"), 960 * MN, C25, 2025, f25),
        entry((None, "2026-02-20"), 950 * MN, C26, 2025, f26),
    ]
    return {"cik": int(CRM_CIK), "entityName": "Salesforce, Inc.", "facts": {
        "us-gaap": {
            REV.split(":")[1]: {"units": {"USD": revenue}},
            ASSETS.split(":")[1]: {"units": {"USD": assets}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": shares}}},
    }}


def units(doc: dict, concept: str, unit: str = "USD") -> list[dict]:
    """The entry list of one concept in one unit, in place."""
    taxonomy, tag = concept.split(":")
    return doc["facts"][taxonomy][tag]["units"][unit]


def december_doc(fys: list[int]) -> dict:
    """A synthetic December filer: one 10-K per ``fy``, the years ending in that ``fy``."""
    revenue = []
    for i, fy in enumerate(fys):
        accn = f"0000900201-{i:02d}-000001"
        filed = f"{fy + 1}-02-20"
        revenue.append(entry((f"{fy}-01-01", f"{fy}-12-31"), (100 + i) * MN, accn, fy, filed))
        prior = (f"{fy - 1}-01-01", f"{fy - 1}-12-31")
        revenue.append(entry(prior, (99 + i) * MN, accn, fy, filed))
    return {"cik": int(SYN_CIK), "entityName": "Synthetic Co", "facts": {
        "us-gaap": {"Revenues": {"units": {"USD": revenue}}}}}


class FakeSecSource(SecSource):
    """Serves one companyfacts document. Frames are unused."""

    def __init__(self, doc: dict) -> None:
        self.doc = doc

    def company_facts(self, cik: str) -> dict:
        return self.doc

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("fiscal-label-alignment must not read frames")


class NoFactStore(FactStoreSource):
    """A fact store with no value for anything, so a key stays single-source."""

    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


def crm_inventory() -> dict:
    """CRM's inventory for the suite keyer."""
    spec = {"Revenue": {"concept": REV, "unit": "USD"}}
    return {"CRM": {"cik": CRM_CIK, "years": {y: dict(spec) for y in (2024, 2025, 2026)}}}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The rows, payloads and fakes, as one namespace."""
    return SimpleNamespace(
        CRM_CIK=CRM_CIK, HD_CIK=HD_CIK, WMT_CIK=WMT_CIK, SYN_CIK=SYN_CIK, JAN_CIK=JAN_CIK,
        MN=MN, C23=C23, C24=C24, C25=C25, C26=C26, H24=H24, H25=H25, H26=H26,
        W23=W23, W24=W24, W25=W25, W26=W26, CRM=CRM, HD=HD, WMT=WMT,
        Y24=Y24, Y25=Y25, Y26=Y26, REV=REV, ASSETS=ASSETS,
        filing=filing, run_of=run_of, labels_in_order=labels_in_order, manifest=manifest,
        dei_of=dei_of, entry=entry, crm_companyfacts=crm_companyfacts, units=units,
        december_doc=december_doc, FakeSecSource=FakeSecSource, NoFactStore=NoFactStore,
        crm_inventory=crm_inventory,
    )
