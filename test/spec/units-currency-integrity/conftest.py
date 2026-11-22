"""Fixtures for units-currency-integrity (docs/specs/units-currency-integrity.md).

Two I/O boundaries are faked, nothing else:

- the database under `XbrlFactstoreScreen` (`psycopg2.connect`), following the spec's
  "Fake database contract": `entity_master` -> roster, `mv_derived_metrics` -> one row per
  filer (no unit, like the view), any other SQL -> component fact rows by fact id;
- the fact store under `PlanExecutor` (`XbrlFactstoreLookup.lookup`). `RowStore` answers a
  lookup with the product's own `XbrlFactstoreLookup._build_result` over committed rows,
  so the OK / AMBIGUOUS / unit decision is the lookup's; `ResultStore` answers with a
  given `FactLookupResult` for synthetic property tests.

Every value below is a stored fact read from xbrl_db (read-only) on 2026-11-07; the
accession of each filer's filing is kept beside it.
"""

from __future__ import annotations

import copy
import types

import psycopg2
import pytest

from src.retriever.xbrl.plan_executor import PlanExecutor
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactLookupResult,
    XbrlFactstoreLookup,
)
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen


# --------------------------------------------------------------------------- C5 data
def _table(keys: tuple, text: str) -> list:
    """Parse a two-line-per-filer table into ``(ticker, cik, period_end, accession,
    {component key: (fact_id, unit, value)})`` entries.

    Line 1: ticker, CIK, period end, accession. Line 2: one ``fact_id:unit:value`` per
    key, in ``keys`` order.
    """
    lines = [ln.strip() for ln in text.strip().splitlines()]
    out = []
    for head, facts in zip(lines[0::2], lines[1::2], strict=True):
        ticker, cik, end, accession = head.split()
        comps = {}
        for key, item in zip(keys, facts.split(), strict=True):
            fid, unit, value = item.split(":")
            comps[key] = (int(fid), unit, int(value))
        out.append((ticker, cik, end, accession, comps))
    return out


# mv_derived_metrics FreeCashFlow FY2025 (33 filers): (ticker, cik, period_end, accession,
# {component key: (fact_id, unit, value)}). Value = operating_cash_flow - capex.
FCF_FY2025 = _table((
    "operating_cash_flow", "capex"), """
AAPL 0000320193 2025-09-28 0000320193-25-000079
    14617:USD:111482000000 14629:USD:12715000000
ABT 0000001800 2026-01-01 0001628280-26-010185
    239194:USD:9566000000 239197:USD:2171000000
ADBE 0000796343 2025-11-29 0000796343-26-000003
    171247:USD:10031000000 171262:USD:179000000
AMD 0000002488 2025-12-28 0000002488-26-000018
    114827:USD:7709000000 114830:USD:974000000
AMZN 0001018724 2026-01-01 0001018724-26-000004
    8305:USD:139514000000 8308:USD:131819000000
AVGO 0001730168 2025-11-03 0001730168-25-000121
    26771:USD:27537000000 26780:USD:623000000
CEG 0001868275 2026-01-01 0001868275-26-000032
    126172:USD:4237000000 126175:USD:2949000000
COST 0000909832 2025-09-01 0000909832-25-000101
    210018:USD:13335000000 210021:USD:5498000000
CRM 0001108524 2025-02-01 0001108524-25-000006
    175504:USD:13092000000 175525:USD:658000000
CSCO 0000858877 2025-07-27 0000858877-25-000111
    165937:USD:14193000000 165958:USD:905000000
GOOGL 0001652044 2026-01-01 0001652044-26-000018
    34566:USD:164713000000 34569:USD:91447000000
HD 0000354950 2026-02-02 0001628280-26-019436
    213686:USD:16325000000 213689:USD:3679000000
INTC 0000050863 2025-12-28 0000050863-26-000011
    157088:USD:9697000000 157091:USD:14646000000
KO 0000021344 2026-01-01 0001628280-26-010047
    195561:USD:7408000000 195576:USD:2112000000
LLY 0000059478 2026-01-01 0000059478-26-000013
    223716:USD:16813000000 223719:USD:7841000000
LMT 0000936468 2026-01-01 0001628280-26-004195
    249229:USD:8557000000 249232:USD:1649000000
MCD 0000063908 2026-01-01 0000063908-26-000035
    217793:USD:10551000000 217796:USD:3365000000
META 0001326801 2026-01-01 0001628280-26-003942
    41501:USD:115800000000 41504:USD:69691000000
MRK 0000310158 2026-01-01 0000310158-26-000063
    232146:USD:16472000000 232149:USD:4112000000
MSFT 0000789019 2025-07-01 0000950170-25-100235
    49019:USD:136162000000 49046:USD:64551000000
MU 0000723125 2025-08-29 0000723125-25-000028
    57520:USD:17525000000 57523:USD:15857000000
NFLX 0001065280 2026-01-01 0001065280-26-000034
    188043:USD:10149273000 188046:USD:688220000
NVDA 0001045810 2025-01-27 0001045810-25-000023
    64620:USD:64089000000 64635:USD:3236000000
ORCL 0001341439 2025-06-01 0000950170-25-087926
    72236:USD:20821000000 72248:USD:21215000000
PG 0000080424 2025-07-01 0000080424-25-000076
    202073:USD:17817000000 202076:USD:3773000000
QCOM 0000804328 2025-09-29 0000804328-25-000085
    179291:USD:14012000000 179294:USD:1192000000
TSLA 0001318605 2026-01-01 0001628280-26-003952
    113195:USD:14747000000 113198:USD:8527000000
TSM 0001046179 2026-01-01 0001628280-26-025362
    91562:TWD:2274975600000 91586:TWD:1272410500000
TXN 0000097476 2026-01-01 0000097476-26-000059
    183847:USD:7153000000 183850:USD:4550000000
UNP 0000100885 2026-01-01 0000100885-26-000037
    243973:USD:9290000000 243976:USD:3791000000
UPS 0001090727 2026-01-01 0001628280-26-008432
    257565:USD:8450000000 257568:USD:3685000000
VST 0001692819 2026-01-01 0001692819-26-000006
    143578:USD:4070000000 143581:USD:2752000000
WMT 0000104169 2025-02-01 0000104169-25-000021
    206742:USD:36443000000 206745:USD:23783000000
""")

# TSM FY2025 USD convenience translations in the same 20-F (0001628280-26-025362).
TSM_FY2025_USD_OCF = (91563, "USD", 72520700000)
TSM_FY2025_USD_CAPEX = (91587, "USD", 40561400000)

# mv_derived_metrics FreeCashFlowMarginPct FY2024 (32 filers). Value =
# (operating_cash_flow - capex) / revenue.
FCF_MARGIN_FY2024 = _table((
    "operating_cash_flow", "capex", "revenue"), """
AAPL 0000320193 2024-09-29 0000320193-24-000123
    13482:USD:118254000000 13494:USD:9447000000 13253:USD:391035000000
ABT 0000001800 2025-01-01 0001628280-25-007110
    237304:USD:8558000000 237307:USD:2207000000 237178:USD:41950000000
ADBE 0000796343 2024-11-30 0000796343-25-000004
    169817:USD:8056000000 169832:USD:183000000 169598:USD:21505000000
AMD 0000002488 2024-12-29 0000002488-25-000012
    19367:USD:3041000000 19370:USD:636000000 19102:USD:25785000000
AMZN 0001018724 2025-01-01 0001018724-25-000004
    6794:USD:115877000000 6797:USD:82999000000 6854:USD:637959000000
AVGO 0001730168 2024-11-04 0001730168-24-000139
    24955:USD:19962000000 24964:USD:548000000 24799:USD:51574000000
CEG 0001868275 2025-01-01 0001868275-25-000023
    123087:USD:-2464000000 123090:USD:2565000000 122934:USD:23568000000
COST 0000909832 2024-09-02 0000909832-24-000049
    209037:USD:11339000000 209046:USD:4710000000 209601:USD:254453000000
CRM 0001108524 2024-02-01 0001108524-24-000005
    174074:USD:10234000000 174095:USD:736000000 173871:USD:34857000000
CSCO 0000858877 2024-07-28 0000858877-24-000017
    163576:USD:10880000000 163597:USD:670000000 163417:USD:53803000000
GOOGL 0001652044 2025-01-01 0001652044-25-000014
    32819:USD:125299000000 32822:USD:52535000000 32629:USD:350018000000
HD 0000354950 2025-02-03 0000354950-25-000085
    212217:USD:19810000000 212220:USD:3485000000 212052:USD:159514000000
KO 0000021344 2025-01-01 0000021344-25-000011
    192746:USD:6805000000 192761:USD:2064000000 192558:USD:47061000000
LLY 0000059478 2025-01-01 0000059478-25-000067
    221482:USD:8817900000 221485:USD:5057800000 221194:USD:45042700000
LMT 0000936468 2025-01-01 0000936468-25-000009
    247326:USD:6972000000 247329:USD:1685000000 247141:USD:71043000000
MCD 0000063908 2025-01-01 0000063908-25-000012
    216472:USD:9447000000 216475:USD:2775000000 216241:USD:25920000000
META 0001326801 2025-01-01 0001326801-25-000017
    40221:USD:91328000000 40224:USD:37256000000 40054:USD:164501000000
MRK 0000310158 2025-01-01 0001628280-25-007732
    228803:USD:21468000000 228806:USD:3372000000 228534:USD:64168000000
MSFT 0000789019 2024-07-01 0000950170-24-087843
    47144:USD:118548000000 47171:USD:44477000000 46957:USD:245122000000
MU 0000723125 2024-08-30 0000723125-24-000027
    55853:USD:8507000000 55856:USD:8386000000 55585:USD:25111000000
NFLX 0001065280 2025-01-01 0001065280-25-000044
    186663:USD:7361364000 186666:USD:439538000 186537:USD:39000966000
NVDA 0001045810 2024-01-29 0001045810-24-000029
    63216:USD:28090000000 63228:USD:1069000000 62929:USD:60922000000
ORCL 0001341439 2024-06-01 0000950170-24-075605
    70175:USD:18673000000 70187:USD:6866000000 69847:USD:52961000000
PG 0000080424 2024-07-01 0000080424-24-000083
    200220:USD:19846000000 200223:USD:3322000000 199901:USD:84039000000
QCOM 0000804328 2024-09-30 0000804328-24-000075
    178079:USD:12202000000 178082:USD:1041000000 177944:USD:38962000000
TSLA 0001318605 2025-01-01 0001628280-25-003063
    111503:USD:14923000000 111506:USD:11339000000 112741:USD:97690000000
TSM 0001046179 2025-01-01 0001193125-25-083423
    87698:TWD:1826177100000 87716:TWD:956006500000 87103:TWD:2894307700000
TXN 0000097476 2025-01-01 0000097476-25-000007
    182117:USD:6318000000 182120:USD:4820000000 181889:USD:15641000000
UNP 0000100885 2025-01-01 0000100885-25-000042
    242612:USD:9346000000 242615:USD:3452000000 242448:USD:24250000000
UPS 0001090727 2025-01-01 0001090727-25-000019
    254476:USD:10122000000 254479:USD:3909000000 254362:USD:91070000000
VST 0001692819 2025-01-01 0001692819-25-000013
    140418:USD:4563000000 140421:USD:2078000000 140133:USD:17224000000
WMT 0000104169 2024-02-01 0000104169-24-000056
    205341:USD:35726000000 205344:USD:20606000000 205006:USD:648125000000
""")

FORMULAS = {
    "FreeCashFlow": "CashFromOperations - Capex",
    "FreeCashFlowMarginPct": "(CashFromOperations - Capex) / Revenue",
}


def _definition(metric: str, comps: dict) -> float:
    """S4's definition over the component facts' values (the view computes the same)."""
    ocf, capex = comps["operating_cash_flow"][2], comps["capex"][2]
    if metric == "FreeCashFlow":
        return float(ocf - capex)
    return (ocf - capex) / comps["revenue"][2]


def build_store(metric: str, fy: int, rows: list) -> dict:
    """A fake-DB store: ``{"metric", "derived": {ticker: row}, "facts": {fact_id: row}}``.

    ``rows`` are entries shaped like ``FCF_FY2025``. Each derived row carries no unit,
    like ``mv_derived_metrics``; each component fact is a ``mv_financial_facts`` row.
    """
    derived: dict = {}
    facts: dict = {}
    for ticker, cik, end, accession, comps in rows:
        derived[ticker] = {
            "ticker": ticker, "cik": cik, "fiscal_year": fy, "fiscal_period": "FY",
            "value": _definition(metric, comps), "metric_name": metric,
            "formula": FORMULAS[metric],
            "component_fact_ids": {k: fid for k, (fid, _, _) in comps.items()},
        }
        for key, (fid, unit, value) in comps.items():
            facts[fid] = {
                "fact_id": fid, "unit": unit, "value": float(value), "ticker": ticker,
                "cik": cik, "fiscal_year": fy, "concept_family": key,
                "period_end": end, "accession_no": accession,
            }
    return {"metric": metric, "derived": derived, "facts": facts}


def _ids_in(params) -> set[int]:
    """Every integer (or digit string) in the query parameters, flattening collections."""
    found: set[int] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, int):
            found.add(v)
        elif isinstance(v, str) and v.isdigit():
            found.add(int(v))
        elif isinstance(v, (list, tuple, set, frozenset)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(params)
    return found


class _Cursor:
    def __init__(self, store: dict, as_dict: bool):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:  # publish_manifest probe: absent, so the gate is inert
            self._rows = [(False,)]
            return
        rows: list[dict] = []
        if "entity_master" in sql:
            rows = [{"cik": r["cik"], "ticker": t}
                    for t, r in sorted(self._store["derived"].items())]
        elif "mv_derived_metrics" in sql:
            p = params if isinstance(params, dict) else {}
            fy, metric = p.get("fy"), p.get("metric_name")
            for _, r in sorted(self._store["derived"].items()):
                if fy is not None and r["fiscal_year"] > fy:
                    continue
                if metric is not None and r["metric_name"] != metric:
                    continue
                rows.append(copy.deepcopy(r))
        else:
            ids = _ids_in(params)
            facts = self._store["facts"]
            chosen = [facts[i] for i in sorted(ids) if i in facts] if ids else [
                facts[i] for i in sorted(facts)]
            rows = [dict(f) for f in chosen]
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store: dict):
        self._store = store

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_units_currency_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def run_derived_screen(store: dict, fy: int, top_n=None):
    """Run ``derived:<store metric>`` for ``fy`` over the fake database."""
    step = {"id": "s1", "op": "screen", "fy": fy, "dimension": "NONE",
            "metric_kind": f"derived:{store['metric']}", "order": "desc", "top_n": top_n}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(store))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


# --------------------------------------------------------------------------- C2/C3 data
def _fact(fid, concept, unit, value, dec, start, end, rank=1, family="Revenue",
          accession=None):
    return {"fact_id": fid, "value": float(value), "unit": unit, "decimals": dec,
            "period_type": "duration" if start else "instant",
            "period_start": start, "period_end": end,
            "concept": concept, "concept_family": family, "concept_pref_rank": rank,
            "segment_axis": None, "segment_member": None, "segment_label": None,
            "dimension_labels": None, "html_anchor": None, "page_num": None,
            "section_path": None, "accession_no": accession, "linked_chunk_ids": []}


G24 = "http://fasb.org/us-gaap/2024:"
G22 = "http://fasb.org/us-gaap/2022:"
G25 = "http://fasb.org/us-gaap/2025:"
IFRS24 = "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full:"
AAPL_CIK, TSM_CIK, CEG_CIK = "0000320193", "0001046179", "0001868275"
AMD_CIK, AVGO_CIK = "0000002488", "0001730168"
AMD25 = "0000002488-26-000018"  # AMD FY2025 10-K
AVGO24 = "0001730168-24-000139"  # AVGO FY2024 10-K

# Rows as the lookup query returns them (period_end DESC), per (cik, fy, family).
LOOKUP_ROWS = {
    # AAPL FY2024 10-K 0000320193-24-000123: revenue and its two comparatives.
    (AAPL_CIK, 2024, "Revenue"): [
        _fact(13253, G24 + "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
              391035000000, -6, "2023-10-01", "2024-09-29"),
        _fact(13254, G24 + "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
              383285000000, -6, "2022-09-25", "2023-10-01"),
        _fact(13255, G24 + "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
              394328000000, -6, "2021-09-26", "2022-09-25"),
    ],
    # TSM FY2024 20-F 0001193125-25-083423: TWD with comparatives, USD convenience.
    (TSM_CIK, 2024, "Revenue"): [
        _fact(87103, IFRS24 + "Revenue", "TWD", 2894307700000, -5, "2024-01-01", "2025-01-01"),
        _fact(87104, IFRS24 + "Revenue", "USD", 88268000000, -5, "2024-01-01", "2025-01-01"),
        _fact(87102, IFRS24 + "Revenue", "TWD", 2161735800000, -5, "2023-01-01", "2024-01-01"),
        _fact(87101, IFRS24 + "Revenue", "TWD", 2263891300000, -5, "2022-01-01", "2023-01-01"),
    ],
    # AAPL FY2025 10-K 0000320193-25-000079 (current period only).
    (AAPL_CIK, 2025, "Revenue"): [
        _fact(14388, G25 + "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
              416161000000, -6, "2024-09-29", "2025-09-28")],
    (AAPL_CIK, 2025, "NetIncome"): [
        _fact(14424, G25 + "NetIncomeLoss", "USD", 112010000000, -6, "2024-09-29",
              "2025-09-28", family="NetIncome")],
    (AAPL_CIK, 2025, "EPSDiluted"): [
        _fact(14430, G25 + "EarningsPerShareDiluted", "shares*USD", 7.46, 2, "2024-09-29",
              "2025-09-28", family="EPSDiluted")],
    (AAPL_CIK, 2025, "WeightedAverageSharesDiluted"): [
        _fact(14436, G25 + "WeightedAverageNumberOfDilutedSharesOutstanding", "shares",
              15004697000, -3, "2024-09-29", "2025-09-28",
              family="WeightedAverageSharesDiluted")],
    (AAPL_CIK, 2025, "CashFromOperations"): [
        _fact(14617, G25 + "NetCashProvidedByUsedInOperatingActivities", "USD",
              111482000000, -6, "2024-09-29", "2025-09-28", family="CashFromOperations")],
    (AAPL_CIK, 2025, "Capex"): [
        _fact(14629, G25 + "PaymentsToAcquirePropertyPlantAndEquipment", "USD",
              12715000000, -6, "2024-09-29", "2025-09-28", family="Capex")],
    # CEG FY2022 10-K 0001868275-23-000014.
    (CEG_CIK, 2022, "Revenue"): [
        _fact(116092, G22 + "Revenues", "USD", 24440000000, -6, "2022-01-01", "2023-01-01")],
    # A ProfitLoss lookup: the calendar-year facts and the post-separation 11-month fact
    # end on the same day with different values.
    (CEG_CIK, 2022, "ProfitLoss"): [
        _fact(116143, G22 + "ProfitLoss", "USD", -167000000, -6, "2022-01-01", "2023-01-01",
              family="ProfitLoss"),
        _fact(116152, G22 + "ProfitLoss", "USD", -167000000, -6, "2022-01-01", "2023-01-01",
              family="ProfitLoss"),
        _fact(116197, G22 + "ProfitLoss", "USD", -167000000, -6, "2022-01-01", "2023-01-01",
              family="ProfitLoss"),
        _fact(116516, G22 + "ProfitLoss", "USD", -318000000, -6, "2022-02-01", "2023-01-01",
              family="ProfitLoss"),
    ],
    (CEG_CIK, 2022, "NetIncome"): [
        _fact(116149, G22 + "NetIncomeLoss", "USD", -160000000, -6, "2022-01-01",
              "2023-01-01", family="NetIncome")],
    # AMD FY2025 10-K: the effective tax rate is tagged in the custom unit "Rate"; the
    # statutory rate beside it in "pure". Instants (no start) are at 2025-12-28.
    (AMD_CIK, 2025, "OperatingIncome"): [
        _fact(114585, G25 + "OperatingIncomeLoss", "USD", 3694000000, -6, "2024-12-29",
              "2025-12-28", family="OperatingIncome", accession=AMD25),
        _fact(114586, G25 + "OperatingIncomeLoss", "USD", 1900000000, -6, "2023-12-31",
              "2024-12-29", family="OperatingIncome", accession=AMD25)],
    (AMD_CIK, 2025, "EffectiveTaxRate"): [
        _fact(115827, G25 + "EffectiveIncomeTaxRateContinuingOperations", "Rate", -0.025,
              3, "2024-12-29", "2025-12-28", family="EffectiveTaxRate", accession=AMD25),
        _fact(115852, G25 + "EffectiveIncomeTaxRateContinuingOperations", "Rate", 0.19,
              None, "2023-12-31", "2024-12-29", family="EffectiveTaxRate",
              accession=AMD25)],
    (AMD_CIK, 2025, "StatutoryTaxRate"): [
        _fact(115791, G25 + "EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate",
              "pure", 0.210, 3, "2024-12-29", "2025-12-28", family="StatutoryTaxRate",
              accession=AMD25)],
    (AMD_CIK, 2025, "StockholdersEquity"): [
        _fact(114709, G25 + "StockholdersEquity", "USD", 62999000000, -6, None,
              "2025-12-28", family="StockholdersEquity", accession=AMD25)],
    # The two debt facts are looked up by concept (the test's own family keys).
    (AMD_CIK, 2025, "LongTermDebtNoncurrent"): [
        _fact(114679, G25 + "LongTermDebtNoncurrent", "USD", 2348000000, -6, None,
              "2025-12-28", family="LongTermDebtNoncurrent", accession=AMD25),
        _fact(114680, G25 + "LongTermDebtNoncurrent", "USD", 1721000000, -6, None,
              "2024-12-29", family="LongTermDebtNoncurrent", accession=AMD25)],
    (AMD_CIK, 2025, "LongTermDebtCurrent"): [
        _fact(115446, G25 + "LongTermDebtCurrent", "USD", 874000000, -6, None,
              "2025-12-28", family="LongTermDebtCurrent", accession=AMD25),
        _fact(115447, G25 + "LongTermDebtCurrent", "USD", 0, -6, None, "2024-12-29",
              family="LongTermDebtCurrent", accession=AMD25)],
    # AVGO FY2024 10-K: effective tax rate in "Rate" beside the USD amounts.
    (AVGO_CIK, 2024, "PretaxIncome"): [
        _fact(24844, G24 + "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
              "ExtraordinaryItemsNoncontrollingInterest", "USD", 9916000000, -6,
              "2023-10-30", "2024-11-04", family="PretaxIncome", accession=AVGO24)],
    (AVGO_CIK, 2024, "TaxExpense"): [
        _fact(24847, G24 + "IncomeTaxExpenseBenefit", "USD", 3748000000, -6, "2023-10-30",
              "2024-11-04", family="TaxExpense", accession=AVGO24)],
    (AVGO_CIK, 2024, "EffectiveTaxRate"): [
        _fact(26258, G24 + "EffectiveIncomeTaxRateContinuingOperations", "Rate", 0.378, 3,
              "2023-10-30", "2024-11-04", family="EffectiveTaxRate", accession=AVGO24)],
}


class RowStore:
    """The fact store at its boundary: committed rows, decided by the real lookup logic."""

    def __init__(self, rows: dict | None = None):
        self.rows = rows if rows is not None else LOOKUP_ROWS
        self.calls: list[dict] = []

    def lookup(self, step: dict) -> FactLookupResult:
        self.calls.append(copy.deepcopy(step))
        lk = XbrlFactstoreLookup()
        notes: list[str] = []
        specs, status = lk._parse_dimensions(step.get("dimensions", "NONE"), notes)
        assert status is None
        family = step["concept_family"]
        rows = copy.deepcopy(self.rows.get((step["cik"], step["fy"], family), []))
        return lk._build_result(step["id"], step, step["cik"], step["fy"], family,
                                "duration", specs, rows, notes)


class ResultStore:
    """The fact store at its boundary: a given result per step id (synthetic cases)."""

    def __init__(self, answers: dict):
        self.answers = answers  # step id -> (LookupStatus, value, unit, fact_id)

    def lookup(self, step: dict) -> FactLookupResult:
        status, value, unit, fid = self.answers[step["id"]]
        return FactLookupResult(
            step_id=step["id"], status=status, value=value, unit=unit,
            citation=Citation(fact_id=fid) if fid is not None else None,
            confidence=0.8)


def lookup_step(sid, cik, fy, family, unit=None):
    step = {"id": sid, "op": "lookup", "cik": cik, "fy": fy, "concept_family": family,
            "dimensions": "NONE", "fiscal_period": "FY"}
    if unit is not None:
        step["unit"] = unit
    return step


def execute(store, steps):
    return PlanExecutor(factstore_lookup=store).execute({"steps": steps})


@pytest.fixture(scope="session")
def fx():
    """Everything the tests share, as one namespace (safe with hypothesis)."""
    return types.SimpleNamespace(
        FCF_FY2025=FCF_FY2025, FCF_MARGIN_FY2024=FCF_MARGIN_FY2024,
        TSM_FY2025_USD_OCF=TSM_FY2025_USD_OCF, TSM_FY2025_USD_CAPEX=TSM_FY2025_USD_CAPEX,
        build_store=build_store, run_derived_screen=run_derived_screen,
        LOOKUP_ROWS=LOOKUP_ROWS, RowStore=RowStore, ResultStore=ResultStore,
        lookup_step=lookup_step, execute=execute,
        AAPL_CIK=AAPL_CIK, TSM_CIK=TSM_CIK, CEG_CIK=CEG_CIK, AMD_CIK=AMD_CIK,
        AVGO_CIK=AVGO_CIK,
    )
