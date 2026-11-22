"""C6, C7: a filing's statement period, and the bounds that reach ``fiscal_calendar``.

Oracle: the companyfacts revenue entries of the same accessions (``start``/``end``), which SEC
lists independently of the instance; synthetic instances built by hand in conftest.py.
"""

from __future__ import annotations

import random
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.downloader.sec_filing_downloader import _extract_period_metadata
from src.ingest.sec_edgar.ingest.layer0_populator import Layer0Populator
from src.ingest.sec_edgar.report_period import report_period_bounds

AAPL_FY2023 = "0000320193-23-000106"
INTC_FY2022 = "0000050863-23-000006"
AMZN_Q2_2026 = "0001018724-26-000026"
ORCL_Q3_FY2026 = "0001193125-26-101045"
ORCL_Q1_FY2027 = "0001193125-26-389274"
FORMS = {AAPL_FY2023: "10-K", INTC_FY2022: "10-K", AMZN_Q2_2026: "10-Q",
         ORCL_Q3_FY2026: "10-Q", ORCL_Q1_FY2027: "10-Q"}

# ------------------------------------------------------------------ C6: the pure reader


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("accession", sorted(FORMS))
def test_bounds_equal_the_statement_period(kit, accession):
    # oracle: companyfacts lists the filing's year- or quarter-length revenue period
    form = FORMS[accession]
    assert report_period_bounds(kit.instance_bytes(accession), form) == \
        kit.statement_period(accession, form)


@pytest.mark.criterion("C6")
def test_the_five_periods_by_hand(kit):
    # oracle: companyfacts entries, copied by hand: Apple's 53-week fiscal 2023 (371 days),
    # Intel's 53-week 2022, Amazon's April-June 2026 quarter (its DEI context is January-June),
    # Oracle's December-February and June-August quarters
    got = {acc: report_period_bounds(kit.instance_bytes(acc), form) for acc, form in FORMS.items()}
    assert got == {
        AAPL_FY2023: (date(2022, 9, 25), date(2023, 9, 30)),
        INTC_FY2022: (date(2021, 12, 26), date(2022, 12, 31)),
        AMZN_Q2_2026: (date(2026, 4, 1), date(2026, 6, 30)),
        ORCL_Q3_FY2026: (date(2025, 12, 1), date(2026, 2, 28)),
        ORCL_Q1_FY2027: (date(2026, 6, 1), date(2026, 8, 31)),
    }
    start, end = got[AAPL_FY2023]
    assert (end - start).days + 1 == 371


@pytest.mark.criterion("C6")
def test_no_statement_length_context_is_none(kit):
    # oracle: requirement 6 — without Amazon's April-June context only the year-to-date,
    # trailing and one-day durations end on 2026-06-30, none of them a quarter; an instance
    # without dei:DocumentType has no report period; an 8-K is neither annual nor quarterly,
    # while the same instance read as the 10-K it is has a period
    text = kit.instance_bytes(AMZN_Q2_2026).decode("utf-8")
    without_quarter = re.sub(r"[ \t]*<context id=\"c-19\">.*?</context>\r?\n", "", text, flags=re.S)
    assert without_quarter != text
    assert report_period_bounds(without_quarter.encode("utf-8"), "10-Q") is None
    without_dei = re.sub(r"[ \t]*<dei:DocumentType\b[^>]*>[^<]*</dei:DocumentType>\r?\n", "", text)
    assert without_dei != text
    assert report_period_bounds(without_dei.encode("utf-8"), "10-Q") is None
    assert report_period_bounds(kit.instance_bytes(AAPL_FY2023), "8-K") is None
    assert report_period_bounds(kit.instance_bytes(AAPL_FY2023), "10-K") is not None


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(accession=st.sampled_from(sorted(FORMS)), seed=st.integers(0, 2**32 - 1))
def test_context_order_does_not_matter(kit, accession, seed):
    # oracle: companyfacts as above — metamorphic: the same contexts in any order
    text = kit.instance_bytes(accession).decode("utf-8")
    pattern = re.compile(r"[ \t]*<context id=\"[^\"]+\">.*?</context>\r?\n", re.S)
    blocks = pattern.findall(text)
    shuffled = blocks[:]
    random.Random(seed).shuffle(shuffled)
    first = text.index(blocks[0])
    last = text.index(blocks[-1]) + len(blocks[-1])
    reordered = text[:first] + "".join(shuffled) + text[last:]
    assert report_period_bounds(reordered.encode("utf-8"), FORMS[accession]) == \
        kit.statement_period(accession, FORMS[accession])


@pytest.mark.criterion("C6")
@given(end=st.dates(date(2005, 1, 1), date(2035, 12, 31)), year_days=st.sampled_from([364, 371]),
       quarter_days=st.sampled_from([84, 91, 92, 98]), ytd_quarters=st.integers(1, 3),
       quarterly=st.booleans(), dei_on_ytd=st.booleans(), seed=st.integers(0, 2**32 - 1))
def test_synthetic_instances(kit, end, year_days, quarter_days, ytd_quarters, quarterly,
                             dei_on_ytd, seed):
    # oracle: the construction — the year (annual form) or quarter (10-Q) context without
    # dimensions ending on the DEI context's end; distractors: year-to-date, trailing-year,
    # prior-year, one-day and instant contexts, and dimensional contexts of statement length
    # with other starts
    year_start = end - timedelta(days=year_days - 1)
    q_start = end - timedelta(days=quarter_days - 1)
    ytd_start = end - timedelta(days=quarter_days * ytd_quarters - 1)
    prior_end = end - timedelta(days=364)
    ctx = kit.context_xml
    if quarterly:
        form, expected = "10-Q", (q_start, end)
        dei_ctx = "ytd" if dei_on_ytd else "q"
        contexts = [ctx("q", q_start, end), ctx("ytd", ytd_start, end),
                    ctx("ttm", end - timedelta(days=364), end),
                    ctx("dq", q_start - timedelta(days=7), end, dimensional=True),
                    ctx("pq", q_start - timedelta(days=364), prior_end)]
    else:
        form, expected = "10-K", (year_start, end)
        dei_ctx = "y"
        contexts = [ctx("y", year_start, end), ctx("q4", q_start, end),
                    ctx("dy", year_start - timedelta(days=7), end, dimensional=True),
                    ctx("py", year_start - timedelta(days=364), prior_end)]
    contexts += [ctx("day", end, end), ctx("i", None, end)]
    random.Random(seed).shuffle(contexts)
    instance = kit.instance_xml(contexts, dei_ctx, form, end)
    assert report_period_bounds(instance, form) == expected


# ------------------------------------------------------------------ C7: what reaches the tables


def _write_filing(folder: Path, kit, accession: str, period_start: str | None = None) -> Path:
    """The filing's manifest (as on disk, start optionally replaced) and its instance excerpt."""
    manifest = dict(kit.ALL_MANIFESTS[accession])
    if period_start is not None:
        manifest["period_start"] = period_start
    instance_name = next(f for f in manifest["files"] if f.endswith("_htm.xml"))
    (folder / instance_name).write_bytes(kit.instance_bytes(accession))
    path = folder / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    return path


C7_CASES = {"AAPL": ("0000320193-23-000106", "10-K"), "AMZN": ("0001018724-26-000026", "10-Q")}


@pytest.mark.criterion("C7")
def test_the_registries_read_the_bounds_from_the_saved_instance(kit):
    # oracle: companyfacts — Apple fiscal 2023 is 2022-09-25..2023-09-30 (the manifest says
    # 2022-10-01), Amazon's second quarter of 2026 is 2026-04-01..06-30 (the manifest says 03-31)
    for accession, form in C7_CASES.values():
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_filing(Path(tmp), kit, accession)
            record = Layer0Populator(sec_edgar_root=tmp)._parse_manifest(path)
        assert (record.period_start, record.period_end) == kit.statement_period(accession, form)


@pytest.mark.criterion("C7")
def test_the_downloader_writes_the_bounds_from_the_instance(kit):
    # oracle: companyfacts, as above
    for accession, form in C7_CASES.values():
        manifest = kit.ALL_MANIFESTS[accession]
        instance_name = next(f for f in manifest["files"] if f.endswith("_htm.xml"))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / instance_name).write_bytes(kit.instance_bytes(accession))
            filing = SimpleNamespace(period_of_report=manifest["period_end"])
            meta = _extract_period_metadata(filing, form, Path(tmp), [instance_name])
        start, end = kit.statement_period(accession, form)
        assert (meta["period_start"], meta["period_end"]) == (start.isoformat(), end.isoformat())


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(case=st.sampled_from(sorted(C7_CASES)), written=st.dates(date(2000, 1, 1), date(2030, 1, 1)))
def test_the_manifest_start_does_not_matter_when_the_instance_is_there(kit, case, written):
    # oracle: companyfacts, as above — metamorphic: whatever start an old manifest holds, the
    # record carries the statement period
    accession, form = C7_CASES[case]
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_filing(Path(tmp), kit, accession, written.isoformat())
        record = Layer0Populator(sec_edgar_root=tmp)._parse_manifest(path)
    assert (record.period_start, record.period_end) == kit.statement_period(accession, form)
