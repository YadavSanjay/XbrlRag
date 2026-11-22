"""Spec tests for ingest-accession-identity: a filing's ``accession`` row is identified by
its accession number, not its file-name stem (docs/specs/ingest-accession-identity.md).

All pure: the rows are what the stem query would return; no DB.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import (
    already_ingested,
    is_accession_number,
    pick_accession_row,
    same_entry_path,
)

# AMD FY2025 (2026-10-14): the 10-K and its Part III 10-K/A share amd-20251227.htm.
AMD_10K = "0000002488-26-000018"
AMD_10KA = "0000002488-26-000021"
ROOT = r"X:\filings\AMD\2025"  # synthetic root, same shape as a SEC_EDGAR_ROOT path
AMD_10K_PATH = ROOT + r"\10-K\amd-20251227.htm"
AMD_10KA_PATH = ROOT + r"\10-K_A\amd-20251227.htm"
PLACEHOLDER = "1779318991"  # Arelle's pre-patch value (patch_accession_numbers docstring)

accnos = st.from_regex(r"\d{10}-\d{2}-\d{6}", fullmatch=True)
placeholders = st.one_of(
    st.none(),
    st.just(""),
    st.integers(min_value=0, max_value=10**12).map(str),
    st.from_regex(r"\d{18}", fullmatch=True),
)
urls = st.sampled_from([AMD_10K_PATH, AMD_10KA_PATH, None])


def _is_acc(value) -> bool:
    # The EDGAR format, written out independently of the code under test.
    if not isinstance(value, str):
        return False
    return re.fullmatch(r"\d{10}-\d{2}-\d{6}", value.strip()) is not None


def _norm(path) -> str:
    # C3's normalisation, written out: drop file: and slashes before the drive, / separators.
    s = re.sub(r"^file:/*", "", path.strip(), flags=re.IGNORECASE).replace("\\", "/")
    return s.lower()


def _same(a, b) -> bool:
    return bool(a) and bool(b) and _norm(a) == _norm(b)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_accession_number_format():
    # oracle: the EDGAR accession-number format ##########-##-######
    assert is_accession_number(AMD_10K) is True
    assert is_accession_number(f"  {AMD_10KA}\n") is True
    for bad in (None, "", PLACEHOLDER, "000000248826000018", "0000002488-26-00018",
                "000002488-26-000018", "0000002488_26_000018"):
        assert is_accession_number(bad) is False, bad


@pytest.mark.criterion("C1")
@given(accnos, placeholders)
def test_accession_number_format_for_any_value(good, bad):
    # oracle: the format — every well-formed number passes, every placeholder fails
    assert is_accession_number(good) is True
    assert is_accession_number(bad) is False


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_amd_10k_is_not_the_amendment():
    # oracle: requirement — the stem matches the 10-K/A's row, patched to another filing
    patched_other = [(AMD_10KA_PATH, AMD_10KA)]
    assert already_ingested(AMD_10K, patched_other) is False
    assert already_ingested(AMD_10KA, patched_other) is True
    assert already_ingested(AMD_10K, patched_other + [(AMD_10K_PATH, AMD_10K)]) is True
    assert already_ingested(AMD_10K, patched_other + [(AMD_10K_PATH, PLACEHOLDER)]) is True
    assert already_ingested(AMD_10K, []) is False
    assert already_ingested(None, patched_other) is True  # no accession: stem match, as today


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(accnos, st.lists(st.tuples(urls, accnos), max_size=5),
       st.lists(st.tuples(urls, placeholders), max_size=3), st.randoms(use_true_random=False))
def test_already_ingested_rule_for_any_rows(acc, patched, unpatched, rnd):
    # oracle: the C2 rule written out; row order does not matter
    rows = patched + unpatched
    expected = any(a == acc for _, a in patched) or bool(unpatched)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert already_ingested(acc, rows) is expected
    assert already_ingested(acc, shuffled) is expected
    others = [(u, a) for u, a in patched if a != acc]
    assert already_ingested(acc, others) is False  # only other filings' rows: not ingested


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_entry_path_forms():
    # oracle: Windows path semantics and the two entry_url forms Arelle writes
    uri = "file:///X:/filings/AMD/2025/10-K/amd-20251227.htm"
    assert same_entry_path(AMD_10K_PATH, AMD_10K_PATH) is True
    assert same_entry_path(uri, AMD_10K_PATH) is True
    assert same_entry_path(AMD_10K_PATH.lower() + " ", AMD_10K_PATH) is True
    assert same_entry_path(AMD_10KA_PATH, AMD_10K_PATH) is False
    assert same_entry_path(None, AMD_10K_PATH) is False
    assert same_entry_path(AMD_10K_PATH, None) is False
    assert same_entry_path("", "") is False


segments = st.lists(st.from_regex(r"[A-Za-z0-9_-]{1,8}", fullmatch=True), min_size=1, max_size=5)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from("CDE"), segments, segments)
def test_entry_path_normalisation_for_any_path(drive, parts, other_parts):
    # oracle: metamorphic — separator style, a file: scheme and case never change identity;
    # a different file name does
    win = f"{drive}:\\" + "\\".join(parts) + ".htm"
    posix_uri = f"file:///{drive}:/" + "/".join(parts) + ".htm"
    assert same_entry_path(win, win) is True
    assert same_entry_path(posix_uri, win) is True
    assert same_entry_path(win.upper(), win.lower()) is True
    other = f"{drive}:\\" + "\\".join(other_parts) + ".htm"
    same_file = [p.lower() for p in other_parts] == [p.lower() for p in parts]
    assert same_entry_path(other, win) is same_file


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_amd_patch_picks_the_unpatched_row():
    # oracle: requirement — rows patched to another accession are not candidates
    rows = [(24, AMD_10KA_PATH, AMD_10KA), (31, AMD_10K_PATH, PLACEHOLDER)]
    assert pick_accession_row(AMD_10K, AMD_10K_PATH, rows) == 31
    assert pick_accession_row(AMD_10K, None, rows) == 31
    assert pick_accession_row(AMD_10KA, AMD_10KA_PATH, rows) == 24  # already patched
    assert pick_accession_row(AMD_10K, None, rows[:1]) is None


@pytest.mark.criterion("C4")
def test_patch_disambiguates_by_path_or_gives_up():
    # oracle: requirement — several unpatched rows: the exact path decides; no path, or no
    # unique path match: None
    rows = [(7, AMD_10KA_PATH, PLACEHOLDER), (9, AMD_10K_PATH, "1779319000")]
    assert pick_accession_row(AMD_10K, AMD_10K_PATH, rows) == 9
    assert pick_accession_row(AMD_10KA, AMD_10KA_PATH, rows) == 7
    assert pick_accession_row(AMD_10K, None, rows) is None
    assert pick_accession_row(AMD_10K, ROOT + r"\10-Q\x.htm", rows) is None
    dup = [(5, AMD_10K_PATH, AMD_10K), (3, AMD_10K_PATH, AMD_10K)]
    assert pick_accession_row(AMD_10K, None, dup) == 3  # first by accession_id


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(accnos, st.lists(st.tuples(urls, accnos), max_size=4),
       st.lists(st.tuples(urls, placeholders), max_size=3), urls, st.randoms(use_true_random=False))
def test_pick_rule_for_any_rows(acc, patched, unpatched, path, rnd):
    # oracle: the C4 rule written out; row order does not matter
    rows = [(i + 1, u, a) for i, (u, a) in enumerate(patched + unpatched)]
    own = [r for r in rows if r[2] == acc]
    if own:
        expected = min(r[0] for r in own)
    else:
        cands = [r for r in rows if not _is_acc(r[2])]
        if len(cands) == 1:
            expected = cands[0][0]
        else:
            by_path = [r for r in cands if _same(r[1], path)]
            expected = by_path[0][0] if len(cands) > 1 and len(by_path) == 1 else None
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert pick_accession_row(acc, path, rows) == expected
    assert pick_accession_row(acc, path, shuffled) == expected
    # A row patched to another filing is never picked.
    picked = pick_accession_row(acc, path, rows)
    assert picked is None or next(r for r in rows if r[0] == picked)[2] in (acc,) or \
        not is_accession_number(next(r for r in rows if r[0] == picked)[2])
