"""Unit tests for calc_check beyond the locked spec tests (docs/specs/calc-check-c11.md).

Edge cases of the reference implementation: exception-file validation, typed dimensions,
a negative-weight-only tree, and fractional decimals.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.ingest.sec_edgar.xbrl.calc_check import check_calculations, load_calc_exceptions

SUM = "http://www.xbrl.org/2003/arcrole/summation-item"
HEADER = "accession_no,link_role,parent,period_start,period_end,unit\n"


def _f(fid, concept, value, decimals=0, dims=()):
    return {"fact_id": fid, "concept": concept, "entity": "E", "period_start": None,
            "period_end": "2025-01-01", "dimensions": list(dims), "unit": "USD",
            "value": None if value is None else Decimal(value), "decimals": decimals}


def _arc(child, weight="1", parent="P"):
    return {"arcrole": SUM, "link_role": "R", "parent": parent, "child": child,
            "weight": Decimal(weight)}


class TestLoadCalcExceptions:
    def test_reads_instant_as_none(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text(HEADER + "A,R,P,,2025-01-01,USD\nA,R,P,2024-01-01,2025-01-01,USD\n",
                        encoding="utf-8")
        assert load_calc_exceptions(path) == {
            ("A", "R", "P", None, "2025-01-01", "USD"),
            ("A", "R", "P", "2024-01-01", "2025-01-01", "USD"),
        }

    def test_header_only_is_empty(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text(HEADER, encoding="utf-8")
        assert load_calc_exceptions(path) == frozenset()

    @pytest.mark.parametrize("text", [
        "accession_no,link_role,parent,period_end,unit\nA,R,P,2025-01-01,USD\n",  # bad header
        HEADER + "A,R,P,,2025-01-01\n",  # short row
        HEADER + "A,R,,,2025-01-01,USD\n",  # empty parent
        "",  # no header
    ])
    def test_malformed_file_raises(self, tmp_path, text):
        path = tmp_path / "x.csv"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_calc_exceptions(path)


class TestCheckEdgeCases:
    def test_dimension_order_does_not_matter(self):
        dims = [("ax:B", "m:2"), ("ax:A", "m:1")]
        facts = [_f(1, "P", 5, dims=dims), _f(2, "C", 5, dims=list(reversed(dims)))]
        (r,) = check_calculations(facts, [_arc("C")])
        assert r["status"] == "pass" and r["dimensional"] is True
        assert r["dimensions"] == frozenset(dims)

    def test_negative_weight_only(self):
        # -1 x [2.5, 3.5] = [-3.5, -2.5]; parent -3 at decimals 0 is [-3.5, -2.5].
        (r,) = check_calculations([_f(1, "P", -3), _f(2, "C", 3)], [_arc("C", "-1")])
        assert (r["status"], r["computed_low"], r["computed_high"]) == (
            "pass", Decimal("-3.5"), Decimal("-2.5"))

    def test_positive_decimals(self):
        # 1.25 at decimals 2 is [1.245, 1.255]; the child 1.3 at decimals 1 is [1.25, 1.35].
        (r,) = check_calculations([_f(1, "P", "1.25", 2), _f(2, "C", "1.3", 1)], [_arc("C")])
        assert r["status"] == "pass"
        assert (r["reported_low"], r["reported_high"]) == (Decimal("1.245"), Decimal("1.255"))

    def test_inconsistent_child_alone_still_binds(self):
        facts = [_f(1, "P", 5), _f(2, "C", 5), _f(3, "C", 6)]
        (r,) = check_calculations(facts, [_arc("C")])
        assert r["status"] == "inapplicable" and r["child_fact_ids"] == (2, 3)

    def test_parent_child_of_different_trees_are_independent(self):
        facts = [_f(1, "P", 5), _f(2, "C", 5)]
        arcs = [_arc("C"), {**_arc("C"), "link_role": "R2", "weight": Decimal("-1")}]
        got = check_calculations(facts, arcs)
        assert [(r["link_role"], r["status"]) for r in got] == [("R", "pass"), ("R2", "fail")]
