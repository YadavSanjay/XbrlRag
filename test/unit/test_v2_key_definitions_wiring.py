"""Unit tests for the key-definitions wiring of the v2 suite build.

Spec: docs/specs/key-definitions.md, "Interface" (behaviour on existing code): the statement
witness keys Debt and DeferredRevenue through ``debt_key`` / ``deferred_revenue_key``, ranks
their screen members by ``member_status`` and income members by ``attribution_status`` /
``attribution_member``; the builder records ``key.definition`` and attaches
``case_readings``. The companyfacts documents and FSDS rows are built here, so every expected
value is read off them. Debt and DeferredRevenue use the spec's locked rules (the live file
carries them after part 2); every other family the live rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.eval.keys.base import SecSource
from src.eval.keys.statement_witness import (
    WitnessBlocked,
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import FactStoreSource, IntentDroppedError, _case_record, _Keyer
from src.eval.suites.v2_witness import KeyWitness

REPO = Path(__file__).resolve().parents[2]
V2 = REPO / "configs" / "eval" / "v2"
KD_RULES = REPO / "test" / "spec" / "key-definitions" / "fixtures" / "statement_lines_kd.yaml"
CIKS = {"AAA": "0000900201", "BBB": "0000900202", "CCC": "0000900203", "DDD": "0000900204"}
YEARS = {2023: ("2023-01-01", "2023-12-31", "2024-02-01", "24"),
         2024: ("2024-01-01", "2024-12-31", "2025-02-01", "25")}


def _adsh(ticker: str, year: int) -> str:
    return f"{CIKS[ticker]}-{YEARS[year][3]}-000001"


# ticker -> year -> [(tag, value, BS label or None for a note-only fact)]. Every filer also
# tags Revenues (an annual duration) so its 10-K has a fiscal-year label.
BS: dict[str, dict[int, list[tuple[str, float, str | None]]]] = {
    # current debt lines and a non-current line; no reading
    "AAA": {y: [("CommercialPaper", 100.0 * k, "Commercial paper"),
                ("LongTermDebtCurrent", 200.0 * k, "Current portion of long-term debt"),
                ("ContractWithCustomerLiabilityCurrent", 70.0 * k, "Deferred revenue"),
                ("LiabilitiesCurrent", 900.0, "Total current liabilities"),
                ("LongTermDebtNoncurrent", 3000.0 * k, "Long-term debt"),
                ("OperatingLeaseLiabilityNoncurrent", 50.0, "Operating lease liabilities"),
                ("ContractWithCustomerLiability", 95.0 * k, None)]
            for y, k in ((2023, 1), (2024, 2))},
    # only a non-current debt line; the note's LongTermDebt is larger: note_total reading
    "BBB": {2024: [("LiabilitiesCurrent", 500.0, "Total current liabilities"),
                   ("LongTermDebtNoncurrent", 1000.0, "Long-term debt"),
                   ("LongTermDebt", 1100.0, None)]},
    # no borrowing line at all
    "CCC": {2024: [("LiabilitiesCurrent", 400.0, "Total current liabilities")]},
    # presents no Debt in the inventory; ranked from its presented line
    "DDD": {2024: [("LiabilitiesCurrent", 400.0, "Total current liabilities"),
                   ("LongTermDebtNoncurrent", 2000.0, "Long-term debt")]},
}
# ticker -> [(tag, value, IS label or None)] for 2024 (net income attribution).
IS: dict[str, list[tuple[str, float, str | None]]] = {
    "AAA": [("ProfitLoss", 1000.0, "Net income"), ("NetIncomeLoss", 990.0, None)],
    "BBB": [("ProfitLoss", 500.0, "Net income"), ("NetIncomeLoss", 500.0, None)],
}


def _entry(ticker: str, year: int, value: float, instant: bool) -> dict[str, Any]:
    start, end, filed, _ = YEARS[year]
    e: dict[str, Any] = {"end": end, "val": value, "accn": _adsh(ticker, year), "fy": year,
                         "fp": "FY", "form": "10-K", "filed": filed}
    if not instant:
        e["start"] = start
    return e


def _companyfacts(ticker: str, overrides: dict[str, float] | None = None,
                  comparatives: bool = True) -> dict:
    gaap: dict[str, Any] = {}

    def add(tag: str, entry: dict[str, Any]) -> None:
        gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(entry)

    for year, rows in BS[ticker].items():
        add("Revenues", _entry(ticker, year, 10_000.0, instant=False))
        for tag, value, _label in rows:
            add(tag, _entry(ticker, year, (overrides or {}).get(tag, value), instant=True))
            if comparatives and ticker == "AAA" and year == 2023:  # in the 2024 10-K too
                add(tag, {**_entry(ticker, 2024, value, instant=True), "end": YEARS[2023][1]})
    for tag, value, _label in IS.get(ticker, []):
        add(tag, _entry(ticker, 2024, value, instant=False))
    return {"cik": int(CIKS[ticker]), "entityName": ticker, "facts": {"us-gaap": gaap}}


def _tables() -> dict[str, list[dict[str, str]]]:
    sub, pre, num = [], [], []

    def value_row(adsh: str, tag: str, ddate: str, qtrs: str, value: float) -> None:
        num.append({"adsh": adsh, "tag": tag, "version": "us-gaap/2024", "ddate": ddate,
                    "qtrs": qtrs, "uom": "USD", "segments": "", "coreg": "",
                    "value": f"{value:.4f}"})

    for ticker, years in BS.items():
        for year, rows in years.items():
            adsh = _adsh(ticker, year)
            ddate = YEARS[year][1].replace("-", "")
            sub.append({"adsh": adsh, "cik": str(int(CIKS[ticker])), "name": ticker,
                        "form": "10-K", "period": ddate, "fy": str(year), "fp": "FY",
                        "filed": YEARS[year][2].replace("-", "")})
            value_row(adsh, "Revenues", ddate, "4", 10_000.0)
            for line, (tag, value, label) in enumerate(rows, start=1):
                value_row(adsh, tag, ddate, "0", value)
                if year == 2024 and ticker == "AAA":  # the comparative column
                    prior = dict((t, v) for t, v, _l in BS["AAA"][2023])[tag]
                    value_row(adsh, tag, "20231231", "0", prior)
                if label is not None:
                    pre.append({"adsh": adsh, "report": "2", "line": str(line), "stmt": "BS",
                                "inpth": "0", "rfile": "H", "tag": tag,
                                "version": "us-gaap/2024", "plabel": label, "negating": "0"})
            for line, (tag, value, label) in enumerate(IS.get(ticker, []) if year == 2024
                                                       else [], start=1):
                value_row(adsh, tag, ddate, "4", value)
                if label is not None:
                    pre.append({"adsh": adsh, "report": "4", "line": str(line), "stmt": "IS",
                                "inpth": "0", "rfile": "H", "tag": tag,
                                "version": "us-gaap/2024", "plabel": label, "negating": "0"})
    return {"sub": sub, "pre": pre, "num": num, "tag": []}


class _Sec(SecSource):
    def __init__(self, overrides: dict[str, dict[str, float]] | None = None,
                 comparatives: bool = True) -> None:
        self.overrides = overrides or {}
        self.comparatives = comparatives

    def company_facts(self, cik: str) -> dict:
        ticker = next(t for t, c in CIKS.items() if c == str(cik).zfill(10))
        return _companyfacts(ticker, self.overrides.get(ticker), self.comparatives)

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are not read")


class _NoStore(FactStoreSource):
    def value(self, cik: str, concept: str, unit: str, period_start: str | None,
              period_end: str, accession: str) -> None:
        return None


def _witness() -> KeyWitness:
    rules = load_statement_rules(V2 / "statement_lines.yaml")
    locked = load_statement_rules(KD_RULES)
    rules["Debt"], rules["DeferredRevenue"] = locked["Debt"], locked["DeferredRevenue"]
    return KeyWitness(_tables(), rules, load_note_concepts(V2 / "note_concepts.yaml"),  # type: ignore[arg-type]  # rows built here
                      load_resolutions(V2 / "key_resolutions.yaml"))


def _inv(metrics: dict[str, list[str]]) -> dict[str, Any]:
    """ticker -> families the inventory keys (on a mapped concept the definition ignores)."""
    concepts = {"Debt": "us-gaap:LongTermDebtNoncurrent",
                "DeferredRevenue": "us-gaap:ContractWithCustomerLiabilityCurrent",
                "NetIncome": "us-gaap:ProfitLoss"}
    return {t: {"cik": CIKS[t], "years": {y: {f: {"concept": concepts[f], "unit": "USD"}
                                              for f in fams} for y in BS[t]}}
            for t, fams in metrics.items()}


def _keyer(inv: dict[str, Any], witness: KeyWitness, sec: _Sec | None = None,
           unis: dict | None = None) -> _Keyer:
    return _Keyer(inv, unis or {}, sec or _Sec(), _NoStore(), witness)


def _lookup(ticker: str, metric: str, year: int = 2024) -> Intent:
    return Intent("lookup", tickers=[ticker], fiscal_years=[year], metrics=[metric])


def test_debt_lookup_is_the_sum_of_the_presented_borrowing_lines():
    w = _witness()
    keyer = _keyer(_inv({"AAA": ["Debt"]}), w)
    keyed = keyer.key(_lookup("AAA", "Debt"))
    # 2024: commercial paper 200 + current portion 400 + long-term 6,000; the lease line out
    assert keyed["value"] == 6600.0
    assert keyed["inputs"][0]["concept"] == "s4:debt_total"
    assert keyer.defined_readings(keyed) == {2024: []}
    assert w.definitions_manifest()["by_family_status"] == {"Debt:defined": 1}


def test_a_note_total_reading_reaches_the_case_and_the_record():
    w = _witness()
    keyer = _keyer(_inv({"BBB": ["Debt"]}), w)
    keyed = keyer.key(_lookup("BBB", "Debt"))
    assert keyed["value"] == 1000.0
    readings = keyer.defined_readings(keyed)
    assert readings == {2024: [{"reading": "note_total", "value": 1100.0,
                                "concept": "us-gaap:LongTermDebt", "period_end": "2024-12-31",
                                "accession": _adsh("BBB", 2024)}]}
    from src.eval.core.case import EvalCase

    case = EvalCase(id="c1", question="q", suite="xbrl_v2_dev", kind="lookup",
                    expected_value=1000.0, alt_readings=readings[2024])
    record = _case_record(case, keyed, None, readings, "debt_total")
    assert record["key"]["definition"] == "debt_total"
    assert record["key"]["alternates"] == readings[2024]
    assert record["alt_readings"] == readings[2024]


def test_deferred_revenue_takes_the_note_total():
    keyed = _keyer(_inv({"AAA": ["DeferredRevenue"]}), _witness()).key(
        _lookup("AAA", "DeferredRevenue"))
    # current line 140 alone on the face; the tagged total 190 is not below it
    assert keyed["value"] == 190.0
    assert keyed["inputs"][0]["concept"] == "s4:deferred_revenue_total"


def test_a_comparison_rereads_each_year_from_the_latest_filing():
    keyer = _keyer(_inv({"AAA": ["Debt"]}), _witness())
    keyed = keyer.key(Intent("compute", operation="difference", tickers=["AAA"],
                             fiscal_years=[2023, 2024], metrics=["Debt"]))
    assert keyed["same_filing"] is True
    # 6,600 (2024) - 3,300 (2023, the 2024 10-K's comparative column)
    assert keyed["value"] == 3300.0
    assert {k["accession"] for k in keyed["inputs"]} == {_adsh("AAA", 2024)}
    assert keyer.defined_readings(keyed) is None


def test_a_point_in_time_reads_the_named_filing():
    keyed = _keyer(_inv({"AAA": ["Debt"]}), _witness()).key(
        Intent("point_in_time", tickers=["AAA"], fiscal_years=[2023], metrics=["Debt"],
               filing_year=2024))
    assert keyed["value"] == 3300.0
    assert keyed["inputs"][0]["accession"] == _adsh("AAA", 2024)
    assert keyed["inputs"][0]["fiscal_year"] == 2023


def test_an_undefined_comparative_column_falls_back_to_each_years_own_filing():
    # the 2024 10-K's companyfacts lack the 2023 column: the re-read is not defined, so each
    # year's own 10-K answers, and nothing blocks the build
    w = _witness()
    keyed = _keyer(_inv({"AAA": ["Debt"]}), w, _Sec(comparatives=False)).key(
        Intent("compute", operation="difference", tickers=["AAA"], fiscal_years=[2023, 2024],
               metrics=["Debt"]))
    assert keyed["same_filing"] is False
    assert keyed["value"] == 3300.0
    assert [k["accession"] for k in keyed["inputs"]] == [_adsh("AAA", 2023), _adsh("AAA", 2024)]
    w.raise_if_blocked()


def test_a_mismatching_component_blocks_and_drops():
    w = _witness()
    sec = _Sec({"AAA": {"CommercialPaper": 999.0}})
    with pytest.raises(IntentDroppedError) as err:
        _keyer(_inv({"AAA": ["Debt"]}), w, sec).key(_lookup("AAA", "Debt"))
    assert err.value.args == ("witness_blocked",)
    with pytest.raises(WitnessBlocked, match="component_mismatch"):
        w.raise_if_blocked()


def _duplicate(cf: dict, tag: str, ticker: str, value: float) -> dict:
    """``cf`` with a second entry of ``tag`` in the 2024 10-K at its year end (another frame)."""
    entries = cf["facts"]["us-gaap"][tag]["units"]["USD"]
    [own] = [e for e in entries if e["accn"] == _adsh(ticker, 2024)
             and e["end"] == YEARS[2024][1]]
    entries.append({**own, "frame": "CY2024Q4I", "val": value})
    return cf


def _debt(ticker: str, cf: dict):
    from src.eval.keys.key_definitions import debt_key

    w = _witness()
    return debt_key(w._tables(_adsh(ticker, 2024)), w.rules, cf, CIKS[ticker], 2024,
                    _adsh(ticker, 2024), YEARS[2024][1], "USD")


def test_agreeing_duplicate_facts_are_accepted():
    d = _debt("AAA", _duplicate(_companyfacts("AAA"), "CommercialPaper", "AAA", 200.0))
    assert (d["status"], d["value"]) == ("defined", 6600.0)


@pytest.mark.parametrize(("ticker", "tag"), [
    ("AAA", "CommercialPaper"),   # a statement line's companyfacts value
    ("BBB", "LongTermDebt"),      # the note total behind a reading
])
def test_disagreeing_duplicate_facts_are_refused(ticker, tag):
    from src.eval.keys.key_definitions import member_status

    d = _debt(ticker, _duplicate(_companyfacts(ticker), tag, ticker, 12_345.0))
    assert (d["status"], d["value"], d["key"]) == ("component_mismatch", None, None)
    assert f"us-gaap:{tag}" in d["reason"] and "disagreeing" in d["reason"]
    with pytest.raises(WitnessBlocked):
        member_status(d)


def test_disagreeing_duplicate_facts_block_the_build():
    class _DupSec(_Sec):
        def company_facts(self, cik: str) -> dict:
            return _duplicate(super().company_facts(cik), "CommercialPaper", "AAA", 1.0)

    w = _witness()
    with pytest.raises(IntentDroppedError) as err:
        _keyer(_inv({"AAA": ["Debt"]}), w, _DupSec()).key(_lookup("AAA", "Debt"))
    assert err.value.args == ("witness_blocked",)
    with pytest.raises(WitnessBlocked, match="disagreeing filing facts"):
        w.raise_if_blocked()


def test_own_period_end_is_the_annual_duration_end():
    from src.eval.keys.core import own_period_end

    cf = _companyfacts("AAA")
    assert own_period_end(cf, _adsh("AAA", 2024)) == "2024-12-31"
    assert own_period_end(cf, "0000000000-00-000000") is None


def test_a_debt_screen_ranks_defined_members_and_excludes_the_rest():
    inv = _inv({"AAA": ["Debt"], "BBB": ["Debt"], "CCC": ["Debt"], "DDD": []})
    inv["DDD"]["years"][2024] = {"Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"}}
    keyed = _keyer(inv, _witness(), unis={"u": ("AAA", "BBB", "CCC", "DDD")}).key(
        Intent("screen", operation="top", n=5, universe="u", fiscal_years=[2024],
               metrics=["Debt"]))
    assert [(r["ticker"], r["value"]) for r in keyed["rows"]] == [
        ("AAA", 6600.0), ("DDD", 2000.0), ("BBB", 1000.0)]
    assert keyed["disclosures"]["excluded"] == [{"ticker": "CCC",
                                                 "reason": "metric_not_tagged"}]


def test_a_consolidated_net_income_member_with_a_parent_figure_is_excluded():
    w = _witness()
    inv = _inv({"AAA": ["NetIncome"], "BBB": ["NetIncome"]})
    keyer = _keyer(inv, w)
    statuses = {}
    for t in ("AAA", "BBB"):
        cf = keyer._cf(inv[t])
        key, adsh, annual = keyer._witnessed(cf, inv[t], "NetIncome",
                                             inv[t]["years"][2024]["NetIncome"], 2024)
        statuses[t] = w.member("NetIncome", adsh, key, annual)
    # AAA reports a parent figure 990 beside its consolidated 1,000; BBB's are equal
    assert (statuses["AAA"]["status"], statuses["AAA"]["reason"]) == (
        "definition_excluded", "substitute:includes_noncontrolling_interests")
    assert (statuses["BBB"]["status"], statuses["BBB"]["value"]) == ("ranked", 500.0)
