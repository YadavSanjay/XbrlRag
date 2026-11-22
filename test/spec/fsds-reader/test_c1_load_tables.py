"""C1: ``load_tables`` and ``submission`` read SEC's tab-separated tables as written.

Oracle: SEC readme §4 (UTF-8, tab-delimited, ``\\n``-terminated, lowercase header line, no
quoting) and the committed fixture files' own bytes, split by the conftest's ``raw_rows``.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import load_tables, submission


@pytest.mark.criterion("C1")
def test_rows_match_the_fixture_files_in_directory_order(kit):
    # oracle: the fixture bytes, split on newline and tab by conftest.raw_rows (readme §4)
    tables = load_tables(kit.dirs)
    for name in ("sub", "num", "pre", "tag"):
        assert tables[name] == kit.raw_rows(name), name
    assert len(tables["sub"]) == 9  # nine fixture accessions, one sub row each


@pytest.mark.criterion("C1")
def test_known_cells_are_raw_strings(kit):
    # oracle: AAPL FY2024 sub row and its Net sales num row as SEC wrote them (fixtures/2024q4);
    # the rendered income statement shows Total net sales 391,035 (millions) for 2024.
    tables = load_tables(kit.dirs)
    sub = [r for r in tables["sub"] if r["adsh"] == kit.AAPL_24]
    assert len(sub) == 1
    assert sub[0]["cik"] == "320193"
    assert sub[0]["form"] == "10-K"
    assert sub[0]["period"] == "20240930"
    assert sub[0]["fye"] == "0930"
    rows = [
        r for r in tables["num"]
        if r["adsh"] == kit.AAPL_24
        and r["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
        and r["ddate"] == "20240930" and r["segments"] == "" and r["coreg"] == ""
    ]
    assert [r["value"] for r in rows] == ["391035000000.0000"]
    assert rows[0]["qtrs"] == "4"
    assert rows[0]["uom"] == "USD"


@pytest.mark.criterion("C1")
def test_submission_finds_the_row_or_none(kit):
    # oracle: fixtures/2025q2/sub.tsv holds TSM's 20-F, filed 2025-04-17 (readme: filed yyyymmdd)
    tables = load_tables(kit.dirs)
    row = submission(tables, kit.TSM_24)
    assert row is not None
    assert row["form"] == "20-F"
    assert row["filed"] == "20250417"
    assert row["fy"] == "2024"
    assert submission(tables, "0000000000-00-000000") is None


@pytest.mark.criterion("C1")
def test_missing_file_raises(kit):
    # oracle: requirement 1 — a directory lacking one of the four files raises FileNotFoundError
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        kit.write_tables(d, {
            "sub": (kit.SUB_HEADER, []),
            "num": (kit.NUM_HEADER, []),
            "pre": (kit.PRE_HEADER, []),
        })
        with pytest.raises(FileNotFoundError):
            load_tables([d])


_field = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\t\n\r"),
    max_size=12,
)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    labels=st.lists(_field, min_size=1, max_size=5),
    seed=st.integers(0, 10_000),
)
def test_column_order_and_quotes_do_not_matter(kit, labels, seed):
    # oracle: readme §4 — columns are named by the header line and fields are unquoted, so a
    # permuted header gives the same dicts and '"' survives literally.
    labels = [f'"{x}' for x in labels]  # every label carries a literal quote
    rows = [[kit.AAPL_24, "3", str(i + 1), "IS", "0", "H", f"Tag{i}", "us-gaap/2024", lab, "0"]
            for i, lab in enumerate(labels)]
    perm = list(range(len(kit.PRE_HEADER)))
    random.Random(seed).shuffle(perm)
    expected = [dict(zip(kit.PRE_HEADER, r, strict=True)) for r in rows]
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        empty = {
            "sub": (kit.SUB_HEADER, []),
            "num": (kit.NUM_HEADER, []),
            "tag": (kit.TAG_HEADER, []),
        }
        kit.write_tables(Path(t1), {**empty, "pre": (kit.PRE_HEADER, rows)})
        kit.write_tables(Path(t2), {**empty, "pre": ([kit.PRE_HEADER[j] for j in perm],
                                                      [[r[j] for j in perm] for r in rows])})
        a = load_tables([Path(t1)])["pre"]
        b = load_tables([Path(t2)])["pre"]
    assert a == expected
    assert b == expected


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(order=st.permutations(list(range(6))))
def test_directories_concatenate_in_the_order_given(kit, order):
    # oracle: requirement 1 — rows come in directory order, then file order; nothing deduplicated
    assert len(kit.dirs) == 6  # six fixture quarters
    dirs = [kit.dirs[i] for i in order]
    tables = load_tables(dirs)
    assert tables["num"] == kit.raw_rows("num", dirs)
    assert tables["sub"] == kit.raw_rows("sub", dirs)
