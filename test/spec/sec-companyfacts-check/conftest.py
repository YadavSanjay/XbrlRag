"""Fakes and builders for the sec-companyfacts-check spec tests.

Spec: docs/specs/sec-companyfacts-check.md. The fakes sit only at the I/O seams the spec
names:

- ``FakeSource``: a ``CompanyFactsSource`` returning synthetic companyfacts dicts in the
  API's shape, counting calls per CIK;
- ``FakeStore``: a ``FactStore`` returning synthetic ``FactRecord``s and
  ``metric_components`` rows, recording every call;
- ``FakeClock`` / ``FakeHttp``: the client's ``clock``, ``sleep`` and ``http_get``.

Everything is exposed through one session-scoped ``kit`` fixture so hypothesis tests can use
it without the function-scoped-fixture health check. All data is synthetic, with values
chosen by hand; no network, no data directory, no current date.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.sec_check.base import CompanyFactsSource, FactRecord, FactStore

US_GAAP = "http://fasb.org/us-gaap/2024"
IFRS = "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"

# Synthetic filers: ticker -> 10-digit CIK (shape only; values are not read from anywhere).
CIKS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "META": "0001326801",
    "GOOGL": "0001652044",
    "TSM": "0001046179",
}

# Fiscal-year duration periods, (start, end).
FY24 = ("2023-10-01", "2024-09-28")
FY23 = ("2022-09-25", "2023-09-30")
FY22 = ("2021-09-26", "2022-09-24")


def entry(
    val: float,
    accn: str,
    end: str,
    start: str | None = None,
    *,
    fy: int = 2024,
    fp: str = "FY",
    form: str = "10-K",
    filed: str = "2024-11-01",
) -> dict[str, Any]:
    """One companyfacts entry; an instant has no ``start`` key at all."""
    e: dict[str, Any] = {}
    if start is not None:
        e["start"] = start
    e.update({"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form,
              "filed": filed})
    return e


def companyfacts(cik: str, name: str, facts: dict[str, dict[str, dict[str, list[dict]]]]) -> dict:
    """A companyfacts document: ``facts`` is ``{taxonomy: {concept: {unit: [entry, ...]}}}``."""
    return {
        "cik": int(cik),
        "entityName": name,
        "facts": {
            tax: {
                concept: {"label": concept, "description": f"Synthetic {concept}.",
                          "units": {u: list(es) for u, es in units.items()}}
                for concept, units in concepts.items()
            }
            for tax, concepts in facts.items()
        },
    }


def fact(
    fact_id: int,
    *,
    concept: str,
    value: float,
    accession_no: str,
    period_end: str,
    period_start: str | None = None,
    unit: str = "USD",
    ticker: str = "AAPL",
    cik: str | None = None,
    dimensional: bool = False,
) -> FactRecord:
    """A ``FactRecord`` (a plain dict)."""
    return FactRecord(
        fact_id=fact_id,
        ticker=ticker,
        cik=cik if cik is not None else CIKS[ticker],
        accession_no=accession_no,
        concept=concept,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        value=value,
        dimensional=dimensional,
    )


def scale_doc(doc: dict, k: float) -> dict:
    """A deep copy of a companyfacts document with every entry's ``val`` multiplied by k."""
    out = copy.deepcopy(doc)
    for concepts in out["facts"].values():
        for body in concepts.values():
            for entries in body["units"].values():
                for e in entries:
                    e["val"] = e["val"] * k
    return out


class FakeSource(CompanyFactsSource):
    """Returns a deep copy of the configured document per CIK; records every call."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = {str(c).zfill(10): d for c, d in docs.items()}
        self.calls: list[str] = []

    def company_facts(self, cik: str) -> dict:
        self.calls.append(str(cik).zfill(10))
        return copy.deepcopy(self.docs[str(cik).zfill(10)])


class FakeStore(FactStore):
    """Returns the configured records and components; records every call."""

    def __init__(
        self,
        records: list[FactRecord],
        components: dict[tuple[str, int, str], dict] | None = None,
    ) -> None:
        self.records = {r["fact_id"]: r for r in records}
        self.components = dict(components or {})
        self.fact_calls: list[list[int]] = []
        self.component_calls: list[tuple[str, int, str]] = []

    def facts(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        ids = list(fact_ids)
        self.fact_calls.append(ids)
        return {i: FactRecord(**self.records[i]) for i in ids if i in self.records}

    def metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None:
        self.component_calls.append((ticker, int(fiscal_year), metric))
        row = self.components.get((ticker, int(fiscal_year), metric))
        return copy.deepcopy(row)

    @property
    def called(self) -> bool:
        return bool(self.fact_calls or self.component_calls)


class FakeClock:
    """A manual clock: ``clock()`` reads ``t``; ``sleep(d)`` records d and advances ``t``."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += max(d, 0.0)


class FakeHttp:
    """``http_get(url, headers) -> (status, body)``; records url, headers and start time."""

    def __init__(self, clock: FakeClock | None = None,
                 responses: dict[str, tuple[int, bytes]] | None = None,
                 default: tuple[int, bytes] = (200, b'{"cik": 320193}')) -> None:
        self.clock = clock
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict | None = None) -> tuple[int, bytes]:
        self.calls.append({"url": url, "headers": dict(headers or {}),
                           "t": self.clock.t if self.clock is not None else None})
        return self.responses.get(url, self.default)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The builders and fakes, as one namespace."""
    return SimpleNamespace(
        US_GAAP=US_GAAP,
        IFRS=IFRS,
        CIKS=CIKS,
        FY24=FY24,
        FY23=FY23,
        FY22=FY22,
        entry=entry,
        companyfacts=companyfacts,
        fact=fact,
        scale_doc=scale_doc,
        FakeSource=FakeSource,
        FakeStore=FakeStore,
        FakeClock=FakeClock,
        FakeHttp=FakeHttp,
    )
