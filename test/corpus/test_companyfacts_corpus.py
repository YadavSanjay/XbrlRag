"""Corpus check: our fact values against SEC companyfacts (test strategy, area B).

The corpus test runs only when ``xbrl_db`` is reachable and the companyfacts cache exists
(``data/sec_companyfacts/`` or ``XBRLRAG_COMPANYFACTS_DIR``); it skips otherwise. The
comparison rule and the exceptions file are also tested without a database.

    conda run -n py312 python -m pytest test/corpus -v
"""

from __future__ import annotations

from collections import Counter

import pytest

from test.corpus import companyfacts_check as cf

_CONCEPT = "http://fasb.org/us-gaap/2024:Revenues"


def _fact(**overrides) -> cf.Fact:
    base = dict(fact_id=1, ticker="X", cik="0000000001", accession_no="A-1",
                concept=_CONCEPT, unit="USD", period_start="2024-01-01",
                period_end="2024-12-31", value=8_600_000_000.0, decimals=-8)
    base.update(overrides)
    return cf.Fact(**base)


def _doc(entries: list[dict], local: str = "Revenues", unit: str = "USD") -> dict:
    return {"facts": {"us-gaap": {local: {"units": {unit: entries}}}}}


# --- The comparison rule (no database) -------------------------------------------------------

@pytest.mark.parametrize(("ours", "sec", "decimals", "agree"), [
    (8_600_000_000, 8_625_000_000, -8, True),     # rounded duplicate: 8,625M is 8,600M at -8
    (8_600_000_000, 8_650_000_000, -8, False),    # half away from zero: 8,650M is 8,700M
    (-8_600_000_000, -8_625_000_000, -8, True),
    (8_600_000_000, -8_625_000_000, -8, False),   # a sign flip never agrees
    (8_600_000, 8_600_000_000, -6, False),        # a scale error never agrees
    (0.85, 0.85, 2, True),
    (0.28, 0.2799999999999998, 2, True),          # binary float noise
    (0.00000625, 0.000006, None, False),          # INF decimals compare exactly
    (1_234.0, 1_234.0, None, True),
])
def test_agree_at_decimals(ours, sec, decimals, agree):
    assert cf.agree_at_decimals(ours, sec, decimals) is agree


def test_per_share_unit_maps_to_companyfacts_form():
    assert cf.companyfacts_unit("shares*USD") == "USD/shares"
    assert cf.companyfacts_unit("shares*TWD") == "TWD/shares"
    assert cf.companyfacts_unit("USD") == "USD"
    assert cf.companyfacts_unit("shares") == "shares"


def test_match_needs_same_accession_and_period():
    doc = _doc([{"accn": "A-0", "start": "2024-01-01", "end": "2024-12-31", "val": 1},
                {"accn": "A-1", "start": "2024-01-01", "end": "2024-12-30", "val": 1}])
    assert cf.compare_fact(_fact(), doc)[0] == "other_accession_only"
    assert cf.compare_fact(_fact(period_end="2023-12-31"), doc)[0] == "not_in_companyfacts"


def test_rounded_duplicate_agrees_only_at_decimals():
    doc = _doc([{"accn": "A-1", "start": "2024-01-01", "end": "2024-12-31",
                 "val": 8_625_000_000}])
    assert cf.compare_fact(_fact(), doc)[0] == "agree_at_decimals"
    assert cf.compare_fact(_fact(value=8_625_000_000.0), doc)[0] == "agree"
    status, dis = cf.compare_fact(_fact(value=8_000_000_000.0), doc)
    assert status == "disagree" and dis.sec_values == [8_625_000_000.0]


def test_one_day_duration_matches_companyfacts_instant():
    doc = _doc([{"accn": "A-1", "end": "2022-05-27", "val": 20}],
               local="StockholdersEquityNoteStockSplitConversionRatio1", unit="pure")
    fact = _fact(concept="http://fasb.org/us-gaap/2022:StockholdersEquityNoteStockSplitConversionRatio1",
                 unit="pure", period_start="2022-05-27", period_end="2022-05-27", value=20.0,
                 decimals=None)
    assert cf.compare_fact(fact, doc)[0] == "agree"


def test_drift_reports_new_and_stale():
    listed = _fact(fact_id=1)
    unlisted = _fact(fact_id=2, accession_no="A-2")
    entry = {"accession_no": "A-1", "concept": "us-gaap:Revenues", "unit": "USD",
             "period_start": "2024-01-01", "period_end": "2024-12-31", "decimals": -8,
             "ours": 8_600_000_000, "category": "x"}
    stale_entry = dict(entry, accession_no="A-9")
    result = cf.CheckResult(disagreements=[cf.Disagreement(listed, [1.0]),
                                           cf.Disagreement(unlisted, [1.0])])
    new, stale = cf.drift(result, [entry, stale_entry])
    assert [d.fact.fact_id for d in new] == [2]
    assert stale == [stale_entry]


def test_exceptions_file_is_well_formed():
    entries = cf.load_exceptions()
    keys = [cf.exception_key(e) for e in entries]
    assert len(keys) == len(set(keys)), "duplicate entries"
    assert all(e.get("category") for e in entries), "every entry needs a category"
    assert all(n > 0 for n in cf.load_uncovered().values())


# --- The corpus (needs xbrl_db and the companyfacts cache) ---------------------------------

def _corpus_available() -> str | None:
    """None when the check can run, else the reason to skip."""
    if not cf.cached_ciks(cf.companyfacts_dir()):
        return f"no companyfacts cache at {cf.companyfacts_dir()}"
    try:
        conn = cf.connect_readonly()
    except Exception as exc:
        return f"xbrl_db not reachable: {exc.__class__.__name__}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            name = cur.fetchone()[0]
            if name != "xbrl_db":
                return f"connected to {name}, not xbrl_db"
            cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
    except Exception as exc:
        return f"mv_financial_facts not readable: {exc.__class__.__name__}"
    finally:
        conn.close()
    return None


_SKIP = _corpus_available()
requires_corpus = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


@pytest.fixture(scope="module")
def corpus() -> cf.CheckResult:
    conn = cf.connect_readonly()
    try:
        return cf.run_check(conn, cf.companyfacts_dir())
    finally:
        conn.close()


@requires_corpus
def test_every_disagreement_is_a_reviewed_exception(corpus):
    new, _ = cf.drift(corpus, cf.load_exceptions())
    assert not new, ("disagreements with SEC companyfacts not in companyfacts_exceptions.yaml "
                     "(review each against the filing):\n"
                     + "\n".join(cf.describe(d) for d in new))


@requires_corpus
def test_exceptions_only_shrink(corpus):
    _, stale = cf.drift(corpus, cf.load_exceptions())
    assert not stale, ("entries in companyfacts_exceptions.yaml that no longer disagree; "
                       "remove them:\n" + "\n".join(str(e) for e in stale))


@requires_corpus
def test_uncovered_facts_only_shrink(corpus):
    gaps = cf.uncovered_drift(corpus, cf.load_uncovered())
    assert not gaps, ("uncovered counts differ from companyfacts_exceptions.yaml:\n"
                      + "\n".join(gaps))


@requires_corpus
def test_corpus_was_actually_compared(corpus):
    # Guards against a silently empty pass (a renamed view column, an emptied cache).
    assert corpus.filers_checked
    assert corpus.compared > 0
    assert Counter(corpus.status_counts)["agree"] >= 0.95 * corpus.compared
