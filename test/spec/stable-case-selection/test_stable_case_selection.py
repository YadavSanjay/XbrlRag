"""Spec tests for stable-case-selection: eval cases are chosen per (ticker, fiscal year)
by a fixed hash, not by truncating an alphabetical enumeration
(docs/specs/stable-case-selection.md).

`select_stratified` is pure. The builder tests use a fake fact store — the database is an
I/O boundary — so no live Postgres is needed.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.suites.sql_derived import SqlDerivedBuilder, select_stratified

TICKERS = [
    "AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "META",
    "MSFT", "MU", "NVDA", "ORCL", "TSLA", "TSM",
]
YEARS = [2024, 2025]
CONCEPTS = ["c1", "c2", "c3", "c4", "c5", "c6"]


# --------------------------------------------------------------------------- oracle
def _key(combo: dict) -> str:
    # The selection key the spec defines (C1).
    return "-".join(f"{k}={combo[k]}" for k in sorted(combo))


def _digest(combo: dict) -> str:
    return hashlib.sha1(_key(combo).encode("utf-8")).hexdigest()


def _expected(combos: list[dict], per_stratum: int | None, ticker: str | None = None) -> list[dict]:
    """The C1 rule, written out independently: per stratum, the lowest digests win."""
    if per_stratum is None:
        return list(combos)
    by_stratum: dict[tuple, list[int]] = defaultdict(list)
    for i, c in enumerate(combos):
        by_stratum[(c.get("ticker", ticker), c.get("fy"))].append(i)
    keep: set[int] = set()
    for idxs in by_stratum.values():
        ranked = sorted(idxs, key=lambda i: (_digest(combos[i]), i))
        keep.update(ranked[:per_stratum])
    return [c for i, c in enumerate(combos) if i in keep]


def _stratum(combo: dict, ticker: str | None = None) -> tuple:
    return (combo.get("ticker", ticker), combo.get("fy"))


# --------------------------------------------------------------------------- fake store
class _Cursor:
    def __init__(self, store):
        self._store = store
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        cols, rows = self._store.answer(sql, params or {})
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeStore:
    """A fact store holding one fact per (ticker, fy, concept), except *empty* concepts."""

    def __init__(self, empty: frozenset = frozenset()):
        self._empty = empty
        self._ids: dict[tuple, int] = {}

    def answer(self, sql, params):
        if "publish_manifest" in sql:
            return ["accession_no", "is_published"], [("0000000000-26-000001", True)]
        if "array_agg" in sql:  # fact equivalence classes: none, so singletons
            return ["anchor", "members"], []
        if params.get("concept") in self._empty:
            return ["value", "fact_id"], []
        key = (params.get("ticker"), params.get("fy"), params.get("concept"))
        fact_id = self._ids.setdefault(key, 1000 + len(self._ids))
        return ["value", "fact_id"], [(float(fact_id) * 1e6, fact_id)]

    # connection protocol
    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        pass

    def close(self):
        pass


def _template(tid: str = "t-pnl", **overrides) -> dict:
    template = {
        "id": tid,
        "source": "analyst_queries.sql#1c",
        "kind": "lookup",
        "nl": [
            "What was {company}'s {concept} in fiscal {fy}?",
            "{company} FY{fy} {concept}?",
        ],
        "params": {
            "ticker": "corpus.tickers",
            "fy": "corpus.fiscal_years",
            "concept": {"values": list(CONCEPTS)},
        },
        "sql": (
            "SELECT value, fact_id FROM mv WHERE t=%(ticker)s AND y=%(fy)s "
            "AND c=%(concept)s AND fiscal_period = 'FY'"
        ),
        "expect": {"value": "value", "fact_ids": "fact_id"},
    }
    template.update(overrides)
    return template


def _spec(tier: dict, templates: list[dict] | None = None, tickers=None, years=None) -> dict:
    return {
        "version": "v1",
        "suite_name": "sql_derived",
        "corpus": {
            "tickers": list(tickers if tickers is not None else TICKERS),
            "fiscal_years": list(years if years is not None else YEARS),
        },
        "tiers": {"standard": tier},
        "templates": templates if templates is not None else [_template()],
    }


def _build(spec: dict, store: FakeStore | None = None):
    tickers = spec["corpus"]["tickers"]
    builder = SqlDerivedBuilder(spec, store or FakeStore(), company_names={t: t for t in tickers})
    suite = builder.build("standard")
    return suite, builder.stats


def _combo_of(case) -> dict:
    return {"ticker": case.ticker, "fy": case.fy, "concept": case.concept}


def _per_stratum(suite) -> Counter:
    return Counter((c.ticker, c.fy) for c in suite.cases)


def _uniform(n: int) -> dict:
    return {(t, y): n for t in TICKERS for y in YEARS}


def _all_combos(tickers=TICKERS, years=YEARS, concepts=CONCEPTS) -> list[dict]:
    return [{"ticker": t, "fy": y, "concept": c} for t in tickers for y in years for c in concepts]


combo_st = st.fixed_dictionaries(
    {
        "ticker": st.sampled_from(["AAPL", "MSFT", "TSM"]),
        "fy": st.sampled_from([2024, 2025]),
        "concept": st.text(alphabet="abcdefghij", min_size=1, max_size=6),
    }
)
unique_combos = st.lists(combo_st, max_size=40, unique_by=_key)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_keeps_lowest_digests_per_stratum_in_input_order():
    # oracle: the C1 rule computed with hashlib (_expected) over a hand-built list
    combos = _all_combos(tickers=["AAPL", "MSFT"], concepts=CONCEPTS)
    got = select_stratified(combos, 2)
    assert got == _expected(combos, 2)
    assert len(got) == 2 * 2 * 2  # 2 tickers x 2 years x quota 2, hand count
    # A pinned template ticker stands in for a combo that binds none.
    pinned = [{"fy": y, "concept": c} for y in YEARS for c in CONCEPTS]
    assert select_stratified(pinned, 1, ticker="TSM") == _expected(pinned, 1, ticker="TSM")
    assert len(select_stratified(pinned, 1, ticker="TSM")) == 2


@pytest.mark.criterion("C1")
def test_no_quota_keeps_all_and_bad_quota_raises():
    # oracle: the C1 rule — None means no cap; a quota below 1 is an error
    combos = _all_combos(tickers=["NVDA"])
    assert select_stratified(combos, None) == combos
    with pytest.raises(ValueError):
        select_stratified(combos, 0)


@pytest.mark.criterion("C1")
def test_identical_keys_keep_the_earlier_combo():
    # oracle: the C1 tie rule — equal keys rank by input position
    first = {"ticker": "AAPL", "fy": 2024, "concept": "x"}
    second = {"ticker": "AAPL", "fy": 2024, "concept": "x"}
    got = select_stratified([first, second], 1)
    assert len(got) == 1 and got[0] is first


@pytest.mark.criterion("C1")
@given(unique_combos, st.integers(min_value=1, max_value=5))
def test_matches_rule_for_any_combos(combos, k):
    # oracle: the C1 rule computed with hashlib
    assert select_stratified(combos, k) == _expected(combos, k)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(unique_combos, st.integers(min_value=1, max_value=5), st.randoms(use_true_random=False))
def test_permuting_input_changes_order_only(combos, k, rnd):
    # oracle: metamorphic — selection depends on each combo's key, not its position
    shuffled = list(combos)
    rnd.shuffle(shuffled)
    a = sorted(_key(c) for c in select_stratified(combos, k))
    b = sorted(_key(c) for c in select_stratified(shuffled, k))
    assert a == b
    assert a == sorted(_key(c) for c in _expected(combos, k))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(unique_combos, unique_combos, st.integers(min_value=1, max_value=5))
def test_other_strata_do_not_affect_a_stratum(base, extra, k):
    # oracle: metamorphic — adding combos to other strata leaves AAPL/2024 unchanged
    target = ("AAPL", 2024)
    base = [c for c in base if _stratum(c) == target]
    extra = [c for c in extra if _stratum(c) != target]
    before = select_stratified(base, k)
    after = [c for c in select_stratified(base + extra, k) if _stratum(c) == target]
    assert [_key(c) for c in before] == [_key(c) for c in after]
    assert len(before) == min(k, len(base))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(unique_combos, combo_st, st.integers(min_value=1, max_value=5))
def test_one_new_combo_displaces_at_most_one(combos, new, k):
    # oracle: metamorphic — a single new member can take at most one seat
    assume(_key(new) not in {_key(c) for c in combos})
    s = _stratum(new)
    before = {_key(c) for c in select_stratified(combos, k) if _stratum(c) == s}
    after = {_key(c) for c in select_stratified(combos + [new], k) if _stratum(c) == s}
    assert len(before - after) <= 1
    n = sum(1 for c in combos if _stratum(c) == s)
    assert len(after) == min(k, n + 1)


@pytest.mark.criterion("C2")
def test_concrete_reversal_and_growth_example():
    # oracle: the C1 rule (_expected) on a hand-built AAPL/MSFT list; reversing it and
    # adding an MSFT combo leave AAPL's two picks unchanged
    combos = _all_combos(tickers=["AAPL", "MSFT"], years=[2024])
    aapl = [_key(c) for c in _expected(combos, 2) if c["ticker"] == "AAPL"]
    assert len(aapl) == 2
    grown = list(reversed(combos)) + [{"ticker": "MSFT", "fy": 2024, "concept": "c7"}]
    got = select_stratified(grown, 2)
    assert sorted(_key(c) for c in got if c["ticker"] == "AAPL") == sorted(aapl)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_builder_keeps_k_per_stratum_across_every_ticker():
    # oracle: hand count — 12 tickers x 2 years x 6 concepts = 144 combos; k = 2 keeps
    # 2 x 24 strata = 48 and drops 96. Which 2 per stratum: the C1 rule (_expected).
    suite, stats = _build(_spec({"paraphrases": 1, "max_cases_per_stratum": 2}))
    assert len(suite.cases) == 48
    assert _per_stratum(suite) == _uniform(2)
    assert {c.ticker for c in suite.cases} == set(TICKERS)
    assert sorted(_key(_combo_of(c)) for c in suite.cases) == sorted(
        _key(c) for c in _expected(_all_combos(), 2)
    )
    assert stats.per_template["t-pnl"] == 48
    assert stats.skipped_capped == 96


@pytest.mark.criterion("C3")
def test_combos_without_a_case_use_no_quota_and_paraphrases_follow():
    # oracle: hand count — c1 has no rows anywhere, so each stratum chooses 2 of c2..c6;
    # 2 paraphrases double the cases (96) but not the kept combinations (48).
    store = FakeStore(empty=frozenset({"c1"}))
    suite, stats = _build(_spec({"paraphrases": 2, "max_cases_per_stratum": 2}), store)
    candidates = _all_combos(concepts=CONCEPTS[1:])
    assert len(suite.cases) == 96
    assert sorted({_key(_combo_of(c)) for c in suite.cases}) == sorted(
        _key(c) for c in _expected(candidates, 2)
    )
    assert sum(1 for c in suite.cases if c.id.endswith("#p1")) == 48
    assert stats.per_template["t-pnl"] == 96
    assert stats.skipped_capped == 120 - 48  # 24 strata x 5 concepts, minus those kept


@pytest.mark.criterion("C3")
@given(st.integers(min_value=1, max_value=7))
def test_builder_quota_for_any_k(k):
    # oracle: hand count — each of the 24 strata keeps min(k, 6) of its 6 combos
    suite, stats = _build(_spec({"paraphrases": 1, "max_cases_per_stratum": k}))
    kept = min(k, len(CONCEPTS))
    assert _per_stratum(suite) == _uniform(kept)
    assert stats.skipped_capped == 144 - 24 * kept


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_template_override_applies_to_that_template_only():
    # oracle: hand count — m = 1 gives 24 cases for t-one; t-two keeps k = 2 (48)
    spec = _spec(
        {"paraphrases": 1, "max_cases_per_stratum": 2},
        templates=[_template("t-one", max_per_stratum=1), _template("t-two")],
    )
    suite, stats = _build(spec)
    one = [c for c in suite.cases if c.id.startswith("t-one:")]
    two = [c for c in suite.cases if c.id.startswith("t-two:")]
    assert len(one) == 24 and len(two) == 48
    expected_one = sorted(_key(c) for c in _expected(_all_combos(), 1))
    assert sorted(_key(_combo_of(c)) for c in one) == expected_one
    assert stats.per_template == {"t-one": 24, "t-two": 48}


@pytest.mark.criterion("C4")
@given(st.integers(min_value=1, max_value=7))
def test_template_override_for_any_m(m):
    # oracle: hand count — each stratum keeps min(m, 6) under the override, whatever k is
    tier = {"paraphrases": 1, "max_cases_per_stratum": 3}
    spec = _spec(tier, templates=[_template(max_per_stratum=m)])
    suite, _ = _build(spec)
    kept = min(m, len(CONCEPTS))
    assert _per_stratum(suite) == _uniform(kept)


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_both_caps_in_one_tier_is_an_error():
    # oracle: requirement — two interacting caps are an ambiguous config; fail loud
    spec = _spec({"paraphrases": 1, "max_cases_per_stratum": 2, "max_cases_per_template": 40})
    with pytest.raises(ValueError):
        _build(spec)


@pytest.mark.criterion("C5")
@given(st.integers(min_value=1, max_value=50), st.integers(min_value=1, max_value=500))
def test_both_caps_error_for_any_values(k, cap):
    # oracle: requirement — the error does not depend on the values chosen
    spec = _spec(
        {"paraphrases": 1, "max_cases_per_stratum": k, "max_cases_per_template": cap},
        tickers=["NVDA"],
    )
    with pytest.raises(ValueError):
        _build(spec)


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_case_ids_with_an_added_ticker_match_the_rule():
    # oracle: the C1 rule (_expected) over the 13-ticker combos; a short key is its own
    # case id fragment, so the expected ids are "t-pnl:<key>"
    tickers = TICKERS + ["IBM"]
    suite, _ = _build(_spec({"paraphrases": 1, "max_cases_per_stratum": 2}, tickers=tickers))
    expected = sorted(f"t-pnl:{_key(c)}" for c in _expected(_all_combos(tickers=tickers), 2))
    assert sorted(c.id for c in suite.cases) == expected
    assert len(expected) == 52  # 13 tickers x 2 years x 2


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_reordering_corpus_lists_keeps_the_case_set():
    # oracle: metamorphic — the corpus lists are sets; their order must not matter
    tier = {"paraphrases": 1, "max_cases_per_stratum": 2}
    a, _ = _build(_spec(tier))
    b, _ = _build(_spec(tier, tickers=list(reversed(TICKERS)), years=list(reversed(YEARS))))
    assert sorted(c.id for c in a.cases) == sorted(c.id for c in b.cases)
    assert len(a.cases) == 48  # hand count, as in C3


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.permutations(TICKERS), st.sampled_from(["IBM", "INTC", "QCOM", "ZZZ"]))
def test_adding_a_ticker_leaves_existing_cases_unchanged(order, new_ticker):
    # oracle: metamorphic — a new filer is a new stratum and cannot displace old cases
    tier = {"paraphrases": 1, "max_cases_per_stratum": 2}
    base, _ = _build(_spec(tier, tickers=order))
    grown, _ = _build(_spec(tier, tickers=list(order) + [new_ticker]))
    old_ids = sorted(c.id for c in base.cases)
    assert sorted(c.id for c in grown.cases if c.ticker != new_ticker) == old_ids
    assert sum(1 for c in grown.cases if c.ticker == new_ticker) == 2 * len(YEARS)
    assert len(old_ids) == 48  # hand count, as in C3
