"""Synthetic filings for the s4-lookup-deferred-revenue-debt spec tests.

Spec: docs/specs/s4-lookup-deferred-revenue-debt.md. Every filing here is fictional, shaped
like a corpus balance sheet (K2 adjudication C10/C11): Filer H like HD (separate short-term
borrowings, lease-inclusive debt lines, a policy-note contract-liability total below the
presented line), Filer G like GOOGL (no current debt line, a tagged debt note total, the
current deferred revenue line with the total in the notes), Filer T like TSLA (extension debt
lines, two deferred revenue lines above a smaller tagged rollforward), Filer O like ORCL (the
current line with the non-current part in the notes), Filer M like META (contract liabilities
in the notes only) and Filer F like MCD (a non-current-only deferred revenue line).

One description builds both representations of the same facts:

- the system's rows (``MeasureLine`` in presentation order, ``NoteFact``);
- the key side's FSDS tables (``sub``, ``pre``, ``num``, ``tag``) and SEC companyfacts document,
  so ``debt_key`` / ``deferred_revenue_key`` can be run on the same facts (requirement 9).

The rules are a byte copy of key-definitions' locked ``statement_lines_kd.yaml``
(``fixtures/statement_lines_s4.yaml``, SHA-256 checked below). Nothing outside this directory
is read; no network, no database.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
RULES_PATH = FIXTURES / "statement_lines_s4.yaml"
RULES_SHA256 = "fd520cc8382bfcd9740183dd4b3c0ad6f0ae2b08bfb115b48b92b10cd076634d"

M = 1e6
ADSH = "0009999901-26-000001"
CIK = "0009999901"
FISCAL_YEAR = 2025
PERIOD_END = "2025-12-31"
DDATE = "20251231"
NIL = "nil"  # a filed nil fact (a fact id, no value)
TCL = "Total current liabilities"
LINE_ID0 = 1000
NOTE_ID0 = 2000


def line(label: str, concept: str, value: Any, unit: str = "USD",
         decimals: Any = -6) -> dict[str, Any]:
    """A balance-sheet line description: ``value`` a float, ``NIL``, or None (no fact)."""
    return {"label": label, "concept": concept, "value": value, "unit": unit,
            "decimals": decimals}


def note(concept: str, value: Any, unit: str = "USD", decimals: Any = -6) -> dict[str, Any]:
    """A note-only fact description: ``value`` a float or ``NIL``."""
    return {"concept": concept, "value": value, "unit": unit, "decimals": decimals}


def build(lines: list[dict[str, Any]], notes: list[dict[str, Any]]) -> SimpleNamespace:
    """Both representations of one filing's facts.

    Line ``i`` with a fact gets fact id ``LINE_ID0 + i``; note ``j`` gets ``NOTE_ID0 + j``.
    The system's ``notes`` hold every line fact too (requirement 2: facts on or off a line).
    """
    sys_lines: list[dict[str, Any]] = []
    sys_notes: list[dict[str, Any]] = []
    ids: dict[str, int] = {}
    pre: list[dict[str, str]] = []
    num: list[dict[str, str]] = []
    tag: list[dict[str, str]] = []
    facts: dict[str, dict[str, Any]] = {}

    def version(concept: str) -> str:
        return ADSH if concept.startswith("ext:") else "us-gaap/2025"

    def add_fact(concept: str, value: Any, unit: str) -> None:
        local = concept.split(":", 1)[1]
        num.append({"adsh": ADSH, "tag": local, "version": version(concept), "ddate": DDATE,
                    "qtrs": "0", "uom": unit, "segments": "", "coreg": "",
                    "value": "" if value == NIL else repr(float(value)), "footnote": ""})
        tag.append({"tag": local, "version": version(concept), "custom":
                    "1" if concept.startswith("ext:") else "0", "abstract": "0",
                    "datatype": "monetary", "iord": "I", "crdr": "C", "tlabel": local,
                    "doc": ""})
        if concept.startswith("us-gaap:"):
            node = facts.setdefault(local, {"units": {}})
            node["units"].setdefault(unit, []).append(
                {"end": PERIOD_END, "val": None if value == NIL else float(value),
                 "accn": ADSH, "fy": FISCAL_YEAR, "fp": "FY", "form": "10-K",
                 "filed": "2026-02-01"})

    for i, d in enumerate(lines):
        has_fact = d["value"] is not None
        fid = LINE_ID0 + i if has_fact else None
        if has_fact:
            ids.setdefault(d["concept"], fid)
        value = None if d["value"] in (None, NIL) else float(d["value"])
        row = {"fact_id": fid, "concept": d["concept"], "label": d["label"], "value": value,
               "unit": d["unit"] if has_fact else None,
               "decimals": d["decimals"] if has_fact else None}
        sys_lines.append(row)
        pre.append({"adsh": ADSH, "report": "2", "line": str(i + 1), "stmt": "BS",
                    "inpth": "0", "rfile": "H", "tag": d["concept"].split(":", 1)[1],
                    "version": version(d["concept"]), "plabel": d["label"], "negating": "0"})
        if has_fact:
            add_fact(d["concept"], d["value"], d["unit"])
            sys_notes.append({"fact_id": fid, "concept": d["concept"], "value": value,
                              "unit": d["unit"], "decimals": d["decimals"]})
    for j, d in enumerate(notes):
        fid = NOTE_ID0 + j
        ids.setdefault(d["concept"], fid)
        value = None if d["value"] == NIL else float(d["value"])
        sys_notes.append({"fact_id": fid, "concept": d["concept"], "value": value,
                          "unit": d["unit"], "decimals": d["decimals"]})
        add_fact(d["concept"], d["value"], d["unit"])

    sub = {"adsh": ADSH, "cik": str(int(CIK)), "name": "SYNTHETIC FILER", "form": "10-K",
           "period": DDATE, "fy": str(FISCAL_YEAR), "fp": "FY", "filed": "20260201"}
    return SimpleNamespace(
        lines=sys_lines, notes=sys_notes, ids=ids,
        tables={"sub": [sub], "num": num, "pre": pre, "tag": tag},
        company_facts={"cik": int(CIK), "entityName": "SYNTHETIC FILER",
                       "facts": {"us-gaap": facts}},
    )


# ------------------------------------------------------------------------------ filers
# Values in millions (times M). Hand sums are written in each test.

def filer_h() -> list[dict[str, Any]]:
    """HD-shaped: commercial paper, lease-inclusive current and long-term debt lines."""
    return [
        line("Current liabilities:", "us-gaap:LiabilitiesCurrentAbstract", None),
        line("Accounts payable", "us-gaap:AccountsPayableCurrent", 10_000 * M),
        line("Short-term borrowings", "us-gaap:CommercialPaper", 300 * M),
        line("Current installments of long-term debt",
             "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", 4_600 * M),
        line("Deferred revenue", "us-gaap:ContractWithCustomerLiabilityCurrent", 2_600 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 30_000 * M),
        line("Long-term debt, excluding current installments",
             "us-gaap:LongTermDebtAndCapitalLeaseObligations", 48_500 * M),
        line("Long-term operating lease liabilities",
             "us-gaap:OperatingLeaseLiabilityNoncurrent", 7_800 * M),
        line("Deferred income taxes", "us-gaap:DeferredIncomeTaxLiabilitiesNet", 1_900 * M),
        line("Total liabilities", "us-gaap:Liabilities", 105_700 * M),
    ]


def filer_h_notes() -> list[dict[str, Any]]:
    """HD-shaped notes: the debt note's LongTermDebt (today's answer), a policy-note total."""
    return [note("us-gaap:LongTermDebt", 51_400 * M),
            note("us-gaap:ContractWithCustomerLiability", 1_500 * M)]


def filer_g() -> list[dict[str, Any]]:
    """GOOGL-shaped: no current debt line; the current deferred revenue line only."""
    return [
        line("Accounts payable", "us-gaap:AccountsPayableCurrent", 8_000 * M),
        line("Accrued revenue share", "ext:AccruedRevenueShareCurrent", 4_000 * M),
        line("Deferred revenue", "us-gaap:ContractWithCustomerLiabilityCurrent", 6_600 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 90_000 * M),
        line("Long-term debt", "us-gaap:LongTermDebtNoncurrent", 46_500 * M),
        line("Income taxes payable, non-current", "us-gaap:AccruedIncomeTaxesNoncurrent",
             9_000 * M),
        line("Operating lease liabilities", "us-gaap:OperatingLeaseLiabilityNoncurrent",
             11_000 * M),
    ]


def filer_g_notes() -> list[dict[str, Any]]:
    """GOOGL-shaped notes: the debt note totals and the contract-liability total."""
    return [note("us-gaap:DebtLongtermAndShorttermCombinedAmount", 49_000 * M),
            note("us-gaap:LongTermDebt", 48_800 * M),
            note("us-gaap:ContractWithCustomerLiability", 8_600 * M)]


def filer_t() -> list[dict[str, Any]]:
    """TSLA-shaped: extension debt-and-finance-lease lines; two deferred revenue lines."""
    return [
        line("Accounts payable", "us-gaap:AccountsPayableCurrent", 12_000 * M),
        line("Current portion of debt and finance leases",
             "ext:CurrentPortionOfDebtAndFinanceLeases", 1_600 * M),
        line("Deferred revenue", "us-gaap:ContractWithCustomerLiabilityCurrent", 3_400 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 40_000 * M),
        line("Debt and finance leases, net of current portion",
             "ext:DebtAndFinanceLeasesNetOfCurrentPortion", 6_700 * M),
        line("Deferred revenue, net of current portion",
             "us-gaap:ContractWithCustomerLiabilityNoncurrent", 3_600 * M),
    ]


def filer_t_notes() -> list[dict[str, Any]]:
    """TSLA-shaped notes: a rollforward total below the two face lines."""
    return [note("us-gaap:ContractWithCustomerLiability", 3_900 * M)]


def filer_o() -> list[dict[str, Any]]:
    """ORCL-shaped: the current deferred revenue line; the non-current part in the notes."""
    return [
        line("Notes payable, current", "us-gaap:LongTermDebtCurrent", 7_300 * M),
        line("Deferred revenues", "us-gaap:ContractWithCustomerLiabilityCurrent", 9_300 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 50_000 * M),
        line("Notes payable and other borrowings, non-current", "us-gaap:LongTermDebtNoncurrent",
             85_000 * M),
        line("Income taxes payable", "us-gaap:AccruedIncomeTaxesNoncurrent", 10_000 * M),
    ]


def filer_o_notes() -> list[dict[str, Any]]:
    """ORCL-shaped notes: the non-current part only."""
    return [note("us-gaap:ContractWithCustomerLiabilityNoncurrent", 1_200 * M)]


def filer_m() -> list[dict[str, Any]]:
    """META-shaped: no deferred revenue line."""
    return [
        line("Accounts payable", "us-gaap:AccountsPayableCurrent", 4_000 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 30_000 * M),
        line("Long-term debt", "us-gaap:LongTermDebtNoncurrent", 28_800 * M),
    ]


def filer_f() -> list[dict[str, Any]]:
    """MCD-shaped: one long-term debt line, lease lines, a non-current deferred revenue line."""
    return [
        line("Accounts payable", "us-gaap:AccountsPayableCurrent", 1_000 * M),
        line(TCL, "us-gaap:LiabilitiesCurrent", 4_000 * M),
        line("Long-term debt", "us-gaap:LongTermDebtNoncurrent", 40_000 * M),
        line("Long-term lease liability", "us-gaap:OperatingLeaseLiabilityNoncurrent",
             14_100 * M),
        line("Deferred revenues - initial franchise fees",
             "ext:DeferredRevenuesInitialFranchiseFees", 945 * M),
    ]


_RULES: list[dict] = []


def rules() -> dict:
    """The locked Debt and DeferredRevenue rules (loaded in a test body, never at import)."""
    if not _RULES:
        from src.eval.keys.statement_witness import load_statement_rules

        _RULES.append(load_statement_rules(RULES_PATH))
    return _RULES[0]


def system(family: str, filing: SimpleNamespace, unit: str = "USD"):
    """``defined_measure`` on a built filing."""
    from src.retriever.xbrl.defined_measure import defined_measure

    rule = rules().get(family) or rules()["Debt"]
    return defined_measure(family, filing.lines, filing.notes, rule, unit)


def key(family: str, filing: SimpleNamespace):
    """The key side's ``debt_key`` / ``deferred_revenue_key`` on the same facts."""
    from src.eval.keys.key_definitions import debt_key, deferred_revenue_key

    fn = debt_key if family == "Debt" else deferred_revenue_key
    return fn(filing.tables, rules(), filing.company_facts, CIK, FISCAL_YEAR, ADSH,
              PERIOD_END, "USD")


@pytest.fixture(scope="session")
def s4():
    """Builders, filers and runners; verifies the locked rules copy first."""
    # LF-normalized: a Windows checkout (core.autocrlf) writes CRLF
    digest = hashlib.sha256(RULES_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == RULES_SHA256, "statement_lines_s4.yaml differs from its locked copy"
    return SimpleNamespace(
        M=M, NIL=NIL, TCL=TCL, line=line, note=note, build=build, rules=rules,
        system=system, key=key,
        filer_h=filer_h, filer_h_notes=filer_h_notes, filer_g=filer_g,
        filer_g_notes=filer_g_notes, filer_t=filer_t, filer_t_notes=filer_t_notes,
        filer_o=filer_o, filer_o_notes=filer_o_notes, filer_m=filer_m, filer_f=filer_f,
    )
