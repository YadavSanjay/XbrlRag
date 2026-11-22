"""C3: a key verifies against the candidates of its kind (docs/specs/set-b-dimensional-s1.md, R3).

Keys are written by hand from FSDS ``num`` values (conftest). Set B's ``dimensional`` cases,
member-hinted compute inputs and every H-X item read the lone-member candidates (R1); set B's
``breakdown`` rows read the breakdown candidates (R2). The stores read a fake database (the I/O
boundary) holding the FSDS rows.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import resolve_case
from src.eval.set_b.hx import resolve_hx_case
from src.eval.set_b.hx_pg_store import PostgresHxKeyFactStore
from src.eval.set_b.pg_store import PostgresKeyFactStore

REV = "Revenues"
SPLIT_24 = [("Compute", "38,950", "Compute"), ("Networking", "8,575", "Networking"),
            ("Gaming", "10,447", "Gaming"),
            ("Professional Visualization", "1,553", "ProfessionalVisualization"),
            ("Automotive", "1,091", "Automotive"), ("OEM", "306", "OEMAndOther")]


def _breakdown(cid, fy, rows):
    return {"id": cid, "question": "-", "kind": "breakdown", "ticker": "NVDA", "fy": fy,
            "unit": "USD", "concept": "Revenue",
            "rows": [{"member": m, "expected": v, "scale": "millions"} for m, v in rows]}


def _dimensional(cid, fy, member, expected):
    return {"id": cid, "question": "-", "kind": "dimensional", "ticker": "NVDA", "fy": fy,
            "expected": expected, "scale": "millions", "unit": "USD", "concept": "Revenue",
            "member": member}


def _trend(values):
    return {"id": "x1", "kind": "cross_period", "subtype": "trend", "ticker": "NVDA",
            "unit": "USD", "concept": "Revenue", "member": "Taiwan",
            "rows": [{"fy": fy, "expected": v, "scale": "millions"} for fy, v in values]}


def _store(kit, cls=PostgresKeyFactStore, rows=None):
    return cls(conn=kit.FakeConn(kit.store_rows() if rows is None else rows))


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C3")
def test_a_breakdown_from_the_recast_split_verifies(kit):
    # oracle: FSDS num of 0001045810-26-000021 at the FY2024 period — the six members of its
    # finest split; each row's class is that filing's fact
    got = resolve_case(_breakdown("b1", 2024, [(m, v) for m, v, _ in SPLIT_24]), _store(kit))
    assert got["status"] == "verified"
    want = [[kit.find(kit.NVDA_26, REV, f"ProductOrService={t};", 2024)["fact_id"]]
            for _, _, t in SPLIT_24]
    assert got["classes"] == want


@pytest.mark.criterion("C3")
def test_a_breakdown_from_the_superseded_split_does_not_verify(kit):
    # oracle: FSDS num — the FY2025 10-K's FY2025 countries (US 61,257, Taiwan 20,573,
    # Singapore 23,684, China 17,108, Other 7,875) are not the FY2026 10-K's split; the FY2026
    # 10-K's four (77,482, 23,600, 25,048, 4,367) verify
    old = [("United States", "61,257"), ("Taiwan", "20,573"), ("Singapore", "23,684"),
           ("China", "17,108"), ("Other Countries", "7,875")]
    got = resolve_case(_breakdown("b2", 2025, old), _store(kit))
    assert got["status"] == "not_found"
    assert [i["status"] for i in got["inputs"]] == ["not_found"] * 5
    new = [("United States", "77,482"), ("Taiwan", "23,600"), ("China", "25,048"),
           ("Other Countries", "4,367")]
    assert resolve_case(_breakdown("b3", 2025, new), _store(kit))["status"] == "verified"


@pytest.mark.criterion("C3")
def test_a_lone_member_key_reads_the_lone_member_rule(kit):
    # oracle: FSDS num and the rule by hand — FY2025 Singapore 23,684 (the FY2025 10-K; no later
    # report) verifies as a lone member though no breakdown holds it; Taiwan verifies at the
    # FY2026 10-K's 23,600, not the original 20,573; FY2024 Compute (38,950) is a breakdown row
    # but not a lone member (the FY2024 10-K does not report it)
    store = _store(kit)
    sg = resolve_case(_dimensional("d1", 2025, "Singapore", "23,684"), store)
    assert sg["status"] == "verified"
    assert sg["classes"] == [[kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2025)["fact_id"]]]
    assert resolve_case(_dimensional("d2", 2025, "Taiwan", "23,600"), store)["status"] == \
        "verified"
    assert resolve_case(_dimensional("d3", 2025, "Taiwan", "20,573"), store)["status"] == \
        "not_found"
    assert resolve_case(_dimensional("d4", 2024, "Compute", "38,950"), store)["status"] == \
        "not_found"
    single = resolve_case(_breakdown("b4", 2025, [("Singapore", "23,684"), ("Taiwan", "23,600")]),
                          store)
    assert [i["status"] for i in single["inputs"]] == ["not_found", "verified"]


@pytest.mark.criterion("C3")
def test_an_hx_member_trend_reads_the_latest_reports(kit):
    # oracle: FSDS num — NVIDIA Taiwan revenue FY2023 6,986 (FY2025 10-K), FY2024 14,912 and
    # FY2025 23,600 (FY2026 10-K) verify; as first filed (13,405 and 20,573) they do not
    store = _store(kit, PostgresHxKeyFactStore)
    latest = resolve_hx_case(_trend([(2023, "6,986"), (2024, "14,912"), (2025, "23,600")]),
                             store)
    assert latest["status"] == "verified"
    assert latest["classes"] == [
        [kit.find(kit.NVDA_25, REV, "Geographical=TW;", 2023)["fact_id"]],
        [kit.find(kit.NVDA_26, REV, "Geographical=TW;", 2024)["fact_id"]],
        [kit.find(kit.NVDA_26, REV, "Geographical=TW;", 2025)["fact_id"]]]
    first = resolve_hx_case(_trend([(2023, "6,986"), (2024, "13,405"), (2025, "20,573")]),
                            store)
    assert first["status"] == "not_found"
    assert [i["status"] for i in first["items"]] == ["verified", "not_found", "not_found"]


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C3")
@given(keep=st.sampled_from([(), ("25",), ("26",), ("25", "26")]),
       order=st.randoms(use_true_random=False))
def test_a_breakdown_written_from_the_latest_splitting_filing_verifies(kit, keep, order):
    # oracle: FSDS num and the rule by hand — with any subset of the later 10-Ks in the store,
    # a key written from the latest remaining filing's FY2024 product split (its own fact
    # values and member labels) verifies to exactly that filing's facts, in any row order
    accs = {"25": kit.NVDA_25, "26": kit.NVDA_26}
    kept = [kit.NVDA_23, kit.NVDA_24] + [accs[k] for k in keep]
    rows = kit.store_rows("NVDA", kept)
    order.shuffle(rows)
    latest = max(kept, key=lambda a: kit.DEI[a]["filed"])
    start, end = kit.PERIOD[("NVDA", "20240131")]
    split = sorted((r for r in rows if r["accession_no"] == latest
                    and r["period_start"] == start and r["period_end"] == kit.next_day(end)
                    and kit.dims_of(r) and kit.dims_of(r)[0][0] == "ProductOrServiceAxis"),
                   key=lambda r: r["fact_id"])
    assert len(split) >= 5
    case = _breakdown("bp", 2024, [(r["segment_label"].replace(" [Member]", ""),
                                    f"{r['value'] / 1e6:,.0f}") for r in split])
    got = resolve_case(case, _store(kit, rows=rows))
    assert got["status"] == "verified"
    assert got["classes"] == [[r["fact_id"]] for r in split]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(order=st.randoms(use_true_random=False))
def test_resolutions_do_not_depend_on_row_order(kit, order):
    # oracle: metamorphic — the hand-checked resolutions above are the same for any row order
    rows = kit.store_rows()
    order.shuffle(rows)
    store = _store(kit, rows=rows)
    assert resolve_case(_breakdown("b1", 2024, [(m, v) for m, v, _ in SPLIT_24]),
                        store)["status"] == "verified"
    assert resolve_case(_dimensional("d3", 2025, "Taiwan", "20,573"), store)["status"] == \
        "not_found"
    hx = _store(kit, PostgresHxKeyFactStore, rows=rows)
    assert resolve_hx_case(_trend([(2024, "14,912"), (2025, "23,600")]), hx)["status"] == \
        "verified"
