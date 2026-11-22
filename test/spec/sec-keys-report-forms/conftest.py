"""Synthetic payloads and fakes for the sec-keys-report-forms spec tests.

Spec: docs/specs/sec-keys-report-forms.md. One made-up December filer (CIK ``0000900201``,
ticker ``TTT``) in the companyfacts shape, values chosen by hand (in millions, times
``MN``). Besides its 10-Ks, a 10-Q and a 10-KT it has 8-Ks, DEF 14A proxies and a PRE 14C
that report some of the same periods with other values, the patterns a trial build of the v2
suites keyed by mistake. Fakes sit only at I/O boundaries: ``FakeSecSource`` (the
``SecSource`` seam), ``NoFactStore`` (the fact store seam), ``FakeTransport`` and
``MemoryStore`` (the HTTP and storage seams of ``LiveSecSource``). Everything is exposed
through one session-scoped ``kit`` fixture. No network, no data directory, no current date.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from hypothesis import strategies as st

from src.eval.keys.base import HttpTransport, SecSource
from src.eval.suites.v2_suite import FactStoreSource
from src.ingest.sec_edgar.storage import FilingStore

CIK = "0000900201"
TICKER = "TTT"
MN = 1_000_000

# Report filings.
A23 = "0000900201-24-000010"   # FY2023 10-K, filed 2024-01-29
A24 = "0000900201-25-000020"   # FY2024 10-K, filed 2025-01-29
Q25 = "0000900201-25-000030"   # Q1 FY2025 10-Q, filed 2025-04-23
# Not report forms.
E25 = "0000900201-25-000005"   # 8-K earnings release, filed 2025-01-22 (before the 10-K)
E25S = "0000900201-25-000021"  # 8-K, filed 2025-01-29 (the 10-K's day)
E25L = "0000900201-25-000033"  # 8-K, filed 2025-07-23
P25 = "0000900201-25-000040"   # DEF 14A proxy, filed 2025-04-20
P26 = "0000900201-26-000050"   # DEF 14A proxy, filed 2026-10-19
C26 = "0000900201-26-000060"   # PRE 14C, filed 2026-03-02
K26 = "0000900201-26-000080"   # 10-KT transition report, filed 2026-02-10
X26 = "0000900201-26-000090"   # an entry with no form at all, filed 2026-05-01

FORM = {A23: "10-K", A24: "10-K", Q25: "10-Q", E25: "8-K", E25S: "8-K", E25L: "8-K",
        P25: "DEF 14A", P26: "DEF 14A", C26: "PRE 14C", K26: "10-KT", X26: None}
FILED = {A23: "2024-01-29", A24: "2025-01-29", Q25: "2025-04-23", E25: "2025-01-22",
         E25S: "2025-01-29", E25L: "2025-07-23", P25: "2025-04-20", P26: "2026-10-19",
         C26: "2026-03-02", K26: "2026-02-10", X26: "2026-05-01"}
FY = {A23: 2023, A24: 2024, Q25: 2025, E25: 2024, E25S: 2024, E25L: 2025, P25: 2024,
      P26: 2025, C26: 2025, K26: 2025, X26: 2025}
FP = {Q25: "Q1"}

REPORT = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-Q", "10-Q/A"})
NON_REPORT = ("10-KT", "10-QT", "8-K", "8-K/A", "DEF 14A", "DEFA14A", "PRE 14A", "PRE 14C",
              "DEF 14C", "6-K", "S-1", "S-4", "424B4", "10-k", " 10-K", "10-K ", "")

FY21 = ("2021-01-01", "2021-12-31")
FY22 = ("2022-01-01", "2022-12-31")
FY23 = ("2023-01-01", "2023-12-31")
FY24 = ("2024-01-01", "2024-12-31")
Q1_25 = ("2025-01-01", "2025-03-31")
END23 = (None, "2023-12-31")
END24 = (None, "2024-12-31")

KEY_FIELDS = {
    "value", "unit", "decimals", "concept", "cik", "period_start", "period_end",
    "fiscal_year", "calendar_year", "accession", "filed", "form", "key_source",
    "selection", "policies", "status", "verified_by",
}


def entry(period: tuple[str | None, str], millions: int | None, accn: str) -> dict:
    """One companyfacts entry; ``period`` is (start, end), start ``None`` for an instant."""
    start, end = period
    e: dict = {"end": end, "val": None if millions is None else millions * MN, "accn": accn,
               "fy": FY[accn], "fp": FP.get(accn, "FY"), "filed": FILED[accn]}
    if FORM[accn] is not None:
        e["form"] = FORM[accn]
    if start is not None:
        e["start"] = start
    return e


def companyfacts() -> dict:
    """A fresh copy of the filer's companyfacts document."""
    usd = {
        # The proxy pattern: pay-versus-performance tables restate net income, later.
        "NetIncomeLoss": [
            entry(FY21, 5_519, P25),
            entry(FY22, 12_556, A23), entry(FY22, 12_583, P25),
            entry(FY23, 14_997, A23), entry(FY23, 14_974, A24),
            entry(FY23, 14_997, P25), entry(FY23, 14_999, P26),
            entry(FY24, 7_091, A24), entry(FY24, 7_130, P26), entry(FY24, 7_100, C26),
            entry(FY24, 1, X26),
        ],
        # The 8-K pattern: an earnings release before the 10-K, a later one after it.
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            entry(FY23, 8_898, A23), entry(FY23, 8_898, A24),
            entry(FY24, 11_300, E25), entry(FY24, 11_339, A24), entry(FY24, 11_340, E25L),
        ],
        # A same-day 8-K with another value; a 10-Q quarter.
        "Revenues": [
            entry(FY23, 96_773, A23), entry(FY23, 96_773, A24),
            entry(FY24, 97_690, A24), entry(FY24, 97_700, E25S),
            entry(Q1_25, 19_335, Q25),
        ],
        # A later transition report.
        "OperatingIncomeLoss": [
            entry(FY23, 8_891, A23), entry(FY24, 7_076, A24), entry(FY24, 7_000, K26),
        ],
        # Report instants; only an 8-K tags it as a duration.
        "StockholdersEquity": [
            entry(END23, 62_634, A23), entry(END23, 62_634, A24),
            entry(END24, 72_913, A24), entry(FY24, 1_000, E25),
        ],
        # Nil in the 10-K; only a proxy has a value.
        "ProfitLoss": [entry(FY23, None, A23), entry(FY23, 15_001, P25)],
    }
    revenues_eur = [entry(FY24, 90_100, E25S)]
    facts = {
        "us-gaap": {tag: {"units": {"USD": entries}} for tag, entries in usd.items()},
        "ecd": {"CoActuallyPaidCompAmt": {"units": {"USD": [
            entry(FY23, 120, P25), entry(FY24, 130, P26)]}}},
    }
    facts["us-gaap"]["Revenues"]["units"]["EUR"] = revenues_eur
    return {"cik": int(CIK), "entityName": "TTT Synthetic Inc.", "facts": facts}


def inventory() -> dict:
    """The v2 inventory of the one filer: metric -> concept and unit, per year."""
    metrics = {"NetIncome": {"concept": "us-gaap:NetIncomeLoss", "unit": "USD"},
               "Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"},
               "ProfitLoss": {"concept": "us-gaap:ProfitLoss", "unit": "USD"}}
    return {TICKER: {"cik": CIK, "years": {2023: dict(metrics), 2024: dict(metrics)}}}


class FakeSecSource(SecSource):
    """Serves one companyfacts document per CIK; records every call."""

    def __init__(self, doc: dict) -> None:
        self.doc = doc
        self.calls: list[tuple] = []

    def company_facts(self, cik: str) -> dict:
        self.calls.append(("company_facts", cik))
        assert int(cik) == int(CIK), f"unexpected CIK {cik}"
        return copy.deepcopy(self.doc)

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are not used by these tests")


class NoFactStore(FactStoreSource):
    """A fact store with no values: every key stays single-source."""

    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


class FakeTransport(HttpTransport):
    """Answers 200 with the mapped body for a known URL, 404 otherwise."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.urls: list[str] = []

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.urls.append(url)
        if url in self.bodies:
            return 200, self.bodies[url]
        return 404, b'{"error": "not found"}'


class MemoryStore(FilingStore):
    """An in-memory ``FilingStore``: the storage seam without a disk."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def write_bytes(self, key: str, data: bytes) -> None:
        self.blobs[str(key).replace("\\", "/").strip("/")] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self.blobs[str(key).replace("\\", "/").strip("/")]

    def exists(self, key: str) -> bool:
        return str(key).replace("\\", "/").strip("/") in self.blobs

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter([k for k in self.blobs if k.endswith("manifest.yaml")])

    @contextmanager
    def localize_dir(self, prefix: str):
        raise NotImplementedError("MemoryStore has no local directories")
        yield


CF_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
WALL = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def cf_bytes(doc: dict) -> bytes:
    """A companyfacts document as the response body the SEC would send."""
    return json.dumps(doc).encode("utf-8")


_NOISE_PERIODS = (FY21, FY22, FY23, FY24, Q1_25, END23, END24, ("2023-07-01", "2024-06-30"))
_NOISE_DATES = ("2021-03-01", "2023-02-01", "2024-01-29", "2025-01-22", "2025-01-29",
                "2025-04-20", "2026-10-19", "2027-06-30")
_NOISE_SLOTS = (("us-gaap", "NetIncomeLoss", "USD"),
                ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
                ("us-gaap", "Revenues", "USD"), ("us-gaap", "Revenues", "EUR"),
                ("us-gaap", "Revenues", "JPY"), ("us-gaap", "OperatingIncomeLoss", "USD"),
                ("us-gaap", "StockholdersEquity", "USD"), ("us-gaap", "ProfitLoss", "USD"),
                ("ecd", "CoActuallyPaidCompAmt", "USD"), ("us-gaap", "Goodwill", "USD"))


def noise_entries(slots: tuple = _NOISE_SLOTS,
                  periods: tuple = _NOISE_PERIODS) -> st.SearchStrategy:
    """Lists of (slot, period, val, filed, form, fy, fp, n) for non-report entries.

    ``form`` is ``None`` (no form field), one of ``NON_REPORT`` or any short text that is not
    a report form; ``slots`` and ``periods`` narrow where the entries land.
    """
    form = st.one_of(st.none(), st.sampled_from(NON_REPORT),
                     st.text(max_size=8).filter(lambda f: f not in REPORT))
    one = st.tuples(
        st.sampled_from(slots), st.sampled_from(periods),
        st.one_of(st.none(), st.integers(min_value=-10**12, max_value=10**13)),
        st.sampled_from(_NOISE_DATES), form,
        st.sampled_from([2021, 2022, 2023, 2024, 2025]), st.sampled_from(["FY", "Q1", "Q2"]),
        st.integers(min_value=0, max_value=999),
    )
    return st.lists(one, min_size=1, max_size=6)


def add_noise(doc: dict, noise: list) -> dict:
    """``doc`` with the generated non-report entries appended (new concepts/units created)."""
    for (taxonomy, tag, unit), (start, end), val, filed, form, fy, fp, n in noise:
        e: dict = {"end": end, "val": val, "accn": f"0000900201-{filed[2:4]}-9{n:05d}",
                   "fy": fy, "fp": fp, "filed": filed}
        if form is not None:
            e["form"] = form
        if start is not None:
            e["start"] = start
        node = doc["facts"].setdefault(taxonomy, {}).setdefault(tag, {"units": {}})
        node["units"].setdefault(unit, []).append(e)
    return doc


def outcome(build) -> tuple:
    """A builder's result, or the class of the key error it raised."""
    from src.eval.keys.core import KeyAmbiguousError, KeyNotFoundError, UnitMismatchError
    try:
        return ("key", build())
    except (KeyNotFoundError, UnitMismatchError, KeyAmbiguousError) as exc:
        return ("error", type(exc).__name__)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The payload, fakes and constants, as one namespace."""
    return SimpleNamespace(
        companyfacts=companyfacts, inventory=inventory, entry=entry,
        FakeSecSource=FakeSecSource, NoFactStore=NoFactStore, FakeTransport=FakeTransport,
        MemoryStore=MemoryStore, cf_bytes=cf_bytes, outcome=outcome, CF_URL=CF_URL,
        noise_entries=noise_entries, add_noise=add_noise,
        WALL=WALL, CIK=CIK, TICKER=TICKER, MN=MN,
        A23=A23, A24=A24, Q25=Q25, E25=E25, E25S=E25S, E25L=E25L, P25=P25, P26=P26,
        C26=C26, K26=K26, X26=X26, REPORT=REPORT, NON_REPORT=NON_REPORT,
        FY21=FY21, FY22=FY22, FY23=FY23, FY24=FY24, Q1_25=Q1_25, END23=END23, END24=END24,
        KEY_FIELDS=KEY_FIELDS,
    )
