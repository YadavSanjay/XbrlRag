"""C4: the resolved path reads the corrected fiscal-year label.

The registry rows are Home Depot's, as read from the live ``filing_registry`` on 2026-10-30:
label 2023/2024/2025 for period ends 2024-01-28, 2025-02-02 and 2026-02-01, under directories
named for the calendar year of the period end. The accession numbers are placeholders. The
filing's text is synthetic and says which year it is. ``PostgresFilingResolver`` is real, on a
fake connection (the DB boundary).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context import PostgresFilingResolver

ROOT = "D:/corpus/sec_edgar"


def _row(label: int, directory_year: int, document: str) -> dict:
    return {
        "ticker": "HD",
        "fiscal_year": label,
        "accession_no": f"accession-{label}",
        "form_type": "10-K",
        "entry_url": f"D:\\corpus\\sec_edgar\\HD\\{directory_year}\\10-K\\{document}",
    }


HD_REGISTRY = [
    _row(2023, 2024, "hd-20240128.htm"),
    _row(2024, 2025, "hd-20250202.htm"),
    _row(2025, 2026, "hd-20260201.htm"),
]
# The label, and the key the entry_url maps to: the path's year is one higher.
HD_KEYS = {
    2023: "HD/2024/10-K/canonical.htm",
    2024: "HD/2025/10-K/canonical.htm",
    2025: "HD/2026/10-K/canonical.htm",
}


def _hd_objects(doc) -> dict[str, str]:
    return {
        HD_KEYS[2023]: doc("Zqyear ended January 28, 2024"),
        HD_KEYS[2024]: doc("Zqyear ended February 2, 2025"),
        HD_KEYS[2025]: doc("Zqyear ended February 1, 2026"),
    }


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    "fiscal_year, wanted",
    [
        (2023, "Zqyear ended January 28, 2024"),
        (2024, "Zqyear ended February 2, 2025"),
        (2025, "Zqyear ended February 1, 2026"),
    ],
)
def test_home_depot_fiscal_n_reads_the_filing_home_depot_calls_fiscal_n(
    make_resolved, make_case, doc, router_reply, registry_conn, fiscal_year, wanted
):
    # oracle: fiscal-label-alignment — HD labels by start year, and the registry carries it
    conn = registry_conn(HD_REGISTRY)
    resolver = PostgresFilingResolver(connect=lambda: conn, sec_edgar_root=ROOT)
    arm, _, adapter, _, store = make_resolved(
        router_text=router_reply(["HD"], [f"fiscal {fiscal_year}"]),
        resolver=resolver,
        objects=_hd_objects(doc),
    )
    answer = arm.answer(
        make_case(f"What were Home Depot's net sales in fiscal {fiscal_year}?", ticker="HD",
                  fy=fiscal_year)
    )
    assert answer.error is None
    assert store.reads == [HD_KEYS[fiscal_year]]
    assert wanted in adapter.user_text()
    # The real SQL selects on the label column, not on a date or a path.
    assert all("fr.fiscal_year = %s" in sql for sql in conn.sql)
    assert answer.selection["filings"][0]["fiscal_year"] == fiscal_year
    assert answer.selection["filings"][0]["key"] == HD_KEYS[fiscal_year]


@pytest.mark.criterion("C4")
def test_most_recent_follows_the_extractors_last_seen_year(
    make_resolved, make_case, doc, router_reply, registry_conn
):
    # oracle: ENTITIES gives HD last_seen_fy 2025 (live value, 2026-10-30), and the extractor
    # anchors "most recent" there; label 2025 is the filing ending 2026-02-01
    conn = registry_conn(HD_REGISTRY)
    resolver = PostgresFilingResolver(connect=lambda: conn, sec_edgar_root=ROOT)
    arm, _, adapter, _, store = make_resolved(
        router_text=router_reply(["HD"], ["most recent fiscal year"]),
        resolver=resolver,
        objects=_hd_objects(doc),
    )
    answer = arm.answer(make_case("What were Home Depot's latest net sales?", ticker="HD", fy=2025))
    assert answer.error is None
    assert store.reads == [HD_KEYS[2025]]
    assert "Zqyear ended February 1, 2026" in adapter.user_text()


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(directory_year=st.integers(2000, 2040), form=st.sampled_from(["10-K", "10-K_A", "20-F"]))
def test_a_key_is_used_verbatim_whatever_its_path_says(
    make_resolved, make_case, doc, router_reply, directory_year, form
):
    # oracle: requirement 6 — the key comes from the resolver; nothing is derived from the
    # ticker and year, so a directory that disagrees with the label changes nothing
    key = f"HD/{directory_year}/{form}/canonical.htm"
    arm, _, adapter, resolver, store = make_resolved(
        router_text=router_reply(["HD"], ["fiscal 2024"]),
        keys={("HD", 2024): key},
        objects={key: doc("Zqthe filing")},
    )
    answer = arm.answer(make_case("What were Home Depot's net sales in fiscal 2024?"))
    assert answer.error is None
    assert resolver.calls == [("HD", 2024)]
    assert store.reads == [key]
    assert answer.selection["filings"][0]["key"] == key
    assert "Zqthe filing" in adapter.user_text()
