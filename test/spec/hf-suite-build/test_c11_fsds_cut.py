"""C11: cutting FSDS quarters to a CIK list.

Oracle: requirement 10 of docs/specs/hf-suite-build.md, applied by hand to the synthetic
tables below (SEC's tab-separated layout, made-up filers 900001 and 900002; the ``pre`` table
deliberately puts ``adsh`` third, so columns must be found by the header). Cut for 900001:
its 10-K and 10-Q in ``sub``; their ``num`` and ``pre`` rows; and the ``tag`` rows those use
(Revenues, NetIncomeLoss and Assets of us-gaap/2023, and the custom CustomAlpha), not
OperatingIncomeLoss (only 900002's), not Unused, and not Revenues of another version.
"""

from __future__ import annotations

import zipfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import load_tables
from src.eval.sec_reference.fsds_cut import cut_fsds, cut_fsds_tables

A1, AQ, B1 = "0000900001-24-000001", "0000900001-24-000007", "0000900002-24-000001"
SUB = ["adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled",
       f"{A1}\t900001\tALPHA CO\t10-K\t20231231\t2023\tFY\t20240212",
       f"{B1}\t900002\tBRAVO CO\t10-K\t20231231\t2023\tFY\t20240213",
       f"{AQ}\t900001\tALPHA CO\t10-Q\t20240331\t2024\tQ1\t20240501"]
NUM = ["adsh\ttag\tversion\tddate\tqtrs\tuom\tvalue",
       f"{A1}\tRevenues\tus-gaap/2023\t20231231\t4\tUSD\t500",
       f"{B1}\tRevenues\tus-gaap/2023\t20231231\t4\tUSD\t400",
       "",
       f"{A1}\tCustomAlpha\t{A1}\t20231231\t4\tUSD\t7",
       f"{AQ}\tNetIncomeLoss\tus-gaap/2023\t20240331\t1\tUSD\t\"9\""]
PRE = ["report\tline\tadsh\tstmt\ttag\tversion\tplabel",
       f"2\t1\t{A1}\tIS\tRevenues\tus-gaap/2023\tRevenue",
       f"2\t3\t{B1}\tIS\tOperatingIncomeLoss\tus-gaap/2023\tOperating income",
       f"2\t1\t{AQ}\tBS\tAssets\tus-gaap/2023\tTotal assets"]
TAG = ["tag\tversion\tcustom\tabstract\tdatatype\ttlabel",
       "Revenues\tus-gaap/2023\t0\t0\tmonetary\tRevenues",
       "OperatingIncomeLoss\tus-gaap/2023\t0\t0\tmonetary\tOperating Income (Loss)",
       "NetIncomeLoss\tus-gaap/2023\t0\t0\tmonetary\tNet Income (Loss)",
       "Unused\tus-gaap/2023\t0\t0\tmonetary\tUnused",
       "Revenues\tus-gaap/2022\t0\t0\tmonetary\tRevenues",
       "Assets\tus-gaap/2023\t0\t0\tmonetary\tAssets",
       f"CustomAlpha\t{A1}\t1\t0\tmonetary\tCustom alpha"]
TABLES = {"sub": SUB, "num": NUM, "pre": PRE, "tag": TAG}


def _bytes(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


RAW = {t: _bytes(lines) for t, lines in TABLES.items()}
EXPECTED_A = {
    "sub": _bytes([SUB[0], SUB[1], SUB[3]]),
    "num": _bytes([NUM[0], NUM[1], NUM[4], NUM[5]]),
    "pre": _bytes([PRE[0], PRE[1], PRE[3]]),
    "tag": _bytes([TAG[0], TAG[1], TAG[3], TAG[6], TAG[7]]),
}
COUNTS_A = {"sub": 2, "num": 3, "pre": 2, "tag": 4}


def _zip(path, tables=RAW) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in tables.items():
            z.writestr(f"{name}.txt", data)
        z.writestr("readme.htm", "<html></html>")


@pytest.mark.criterion("C11")
def test_cut_tables_by_hand():
    # oracle: the hand cut in the module docstring.
    assert cut_fsds_tables(RAW, ["0000900001"]) == EXPECTED_A
    assert cut_fsds_tables(RAW, ["900001"]) == EXPECTED_A
    with pytest.raises(ValueError):
        cut_fsds_tables(RAW, [])


@pytest.mark.criterion("C11")
def test_cut_quarter_zips_into_a_directory(tmp_path):
    # oracle: requirement 10: <out>/<quarter>/<table>.tsv, the hand counts, and the result
    # read by the existing FSDS reader.
    raw = tmp_path / "raw"
    raw.mkdir()
    _zip(raw / "2024q1.zip")
    out = tmp_path / "out"
    assert cut_fsds([raw / "2024q1.zip"], ["0000900001"], out) == {"2024q1": COUNTS_A}
    for table, data in EXPECTED_A.items():
        assert (out / "2024q1" / f"{table}.tsv").read_bytes() == data
    tables = load_tables([out / "2024q1"])
    assert [r["adsh"] for r in tables["sub"]] == [A1, AQ]
    assert {r["cik"] for r in tables["sub"]} == {"900001"}

    _zip(raw / "quarter.zip")
    with pytest.raises(ValueError):
        cut_fsds([raw / "quarter.zip"], ["0000900001"], tmp_path / "bad")
    _zip(raw / "2024q2.zip", {t: d for t, d in RAW.items() if t != "tag"})
    with pytest.raises(FileNotFoundError):
        cut_fsds([raw / "2024q2.zip"], ["0000900001"], tmp_path / "missing")
    with pytest.raises(ValueError):
        cut_fsds([raw / "2024q1.zip"], [], tmp_path / "empty")


def _lines(data: bytes) -> list[bytes]:
    return [ln for ln in data.split(b"\n") if ln]


CIKS = st.sets(st.sampled_from(("900001", "0000900002", "900009")), min_size=1)


@pytest.mark.criterion("C11")
@settings(max_examples=30)
@given(one=CIKS, two=CIKS, pad=st.booleans())
def test_cut_of_a_union_is_the_union_of_cuts(one, two, pad):
    # oracle: requirement 10: every rule keeps a row by membership, so the cut for one | two
    # holds exactly the input rows kept for one or for two, in input order; padding a CIK
    # changes nothing.
    def norm(ciks):
        return {c.zfill(10) if pad else str(int(c)) for c in ciks}

    both = cut_fsds_tables(RAW, norm(one | two))
    left, right = cut_fsds_tables(RAW, norm(one)), cut_fsds_tables(RAW, norm(two))
    for table in TABLES:
        kept = set(_lines(left[table])) | set(_lines(right[table]))
        want = [ln for ln in _lines(RAW[table]) if ln in kept]
        assert _lines(both[table]) == want
        assert _lines(both[table])[0] == _lines(RAW[table])[0]
