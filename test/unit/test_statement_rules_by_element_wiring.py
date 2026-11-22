"""Unit tests for the two optional statement-rule fields in ``statement_witness``.

Spec: docs/specs/statement-rules-by-element.md. The locked tests pin ``load_statement_rules``,
``label_head``, ``line_is_candidate`` and ``select_lines``; these check the wiring one level up:
a key's witness status moves from ``rejected_line`` to ``agree`` exactly when the rule lists
the line's element or strips the qualifier that rejected it. The FSDS rows are built here, so
every expected value is read off them.
"""

from __future__ import annotations

from typing import Any, cast

from src.eval.keys.statement_witness import StatementRule, statement_witness

CIK = "0000900201"
ADSH = "0000900201-25-000001"


def _tables(lines: list[tuple[str, str, str, float, str]]) -> dict[str, list[dict[str, str]]]:
    """One 10-K's FSDS rows: ``(stmt, version, tag, value, plabel)`` per line, instant or FY."""
    sub = [{"adsh": ADSH, "cik": str(int(CIK)), "name": "X", "form": "10-K",
            "period": "20241231", "fy": "2024", "fp": "FY", "filed": "20250201"}]
    pre, num = [], []
    for n, (stmt, version, tag, value, label) in enumerate(lines, start=1):
        num.append({"adsh": ADSH, "tag": tag, "version": version, "ddate": "20241231",
                    "qtrs": "0" if stmt == "BS" else "4", "uom": "USD", "segments": "",
                    "coreg": "", "value": f"{value:.4f}"})
        pre.append({"adsh": ADSH, "report": "2" if stmt == "BS" else "4", "line": str(n),
                    "stmt": stmt, "inpth": "0", "rfile": "H", "tag": tag, "version": version,
                    "plabel": label, "negating": "0"})
    return {"sub": sub, "pre": pre, "num": num, "tag": []}


def _rule(**extra: Any) -> StatementRule:
    base: dict[str, Any] = {"statements": ["BS"], "parenthetical": False, "include": [],
                            "require": [], "exclude": [], "section": None, "prefer": [],
                            "combine": "one", "substitutes": {}}
    return cast(StatementRule, {**base, **extra})


def _key(concept: str, value: float, start: str | None) -> dict[str, Any]:
    return {"form": "10-K", "accession": ADSH, "concept": concept, "value": value, "cik": CIK,
            "fiscal_year": 2024, "period_start": start, "period_end": "2024-12-31",
            "unit": "USD"}


def test_an_element_line_turns_a_rejected_line_into_agree():
    tables = _tables([("BS", "ifrs/2024", "CurrentPortionOfLongtermBorrowings", 60.0,
                       "Long-term liabilities - current portion")])
    key = _key("ifrs-full:CurrentPortionOfLongtermBorrowings", 60.0, None)
    plain = _rule(include=[r"\bborrowings?\b"])
    w = statement_witness(tables, {"Debt": plain}, {}, "Debt", key, ADSH)
    assert (w["status"], w["reason"]) == ("rejected_line",
                                          "Long-term liabilities - current portion")
    listed = _rule(include=[r"\bborrowings?\b"],
                   elements=["ifrs-full:CurrentPortionOfLongtermBorrowings"])
    w = statement_witness(tables, {"Debt": listed}, {}, "Debt", key, ADSH)
    assert (w["status"], w["value"]) == ("agree", 60.0)
    assert [ln["concept"] for ln in w["lines"]] == ["ifrs-full:CurrentPortionOfLongtermBorrowings"]


def test_an_extension_line_with_a_listed_local_name_stays_rejected():
    tables = _tables([("BS", ADSH, "CurrentPortionOfLongtermBorrowings", 60.0, "Current part")])
    key = _key("ext:CurrentPortionOfLongtermBorrowings", 60.0, None)
    rule = _rule(include=[r"\bborrowings?\b"],
                 elements=["ifrs-full:CurrentPortionOfLongtermBorrowings"])
    w = statement_witness(tables, {"Debt": rule}, {}, "Debt", key, ADSH)
    assert w["status"] == "rejected_line"


def test_stripping_a_qualifier_admits_the_line_it_rejected():
    label = "Purchases of property and equipment excluding finance leases, net of sales"
    tables = _tables([("CF", "us-gaap/2024", "PaymentsToAcquirePropertyPlantAndEquipment",
                       8527.0, label)])
    key = _key("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 8527.0, "2024-01-01")
    capex = {"statements": ["CF"], "include": [r"\bpurchases? of property"],
             "exclude": [r"\bleases?\b", r"\bsales?\b"]}
    w = statement_witness(tables, {"Capex": _rule(**capex)}, {}, "Capex", key, ADSH)
    assert (w["status"], w["reason"]) == ("rejected_line", label)
    w = statement_witness(tables, {"Capex": _rule(**capex, strip_qualifiers=True)}, {}, "Capex",
                          key, ADSH)
    assert (w["status"], w["value"], w["lines"][0]["plabel"]) == ("agree", 8527.0, label)
