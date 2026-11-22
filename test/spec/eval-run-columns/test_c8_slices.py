"""C8: accuracy by split / phrasing family / key source, and intent stability
(requirements 9 and 10)."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.run_columns import intent_stability, slice_metrics, slice_report

FIELDS = ["split", "intent_id", "phrasing_family", "template_id", "key_source",
          "key_status"]
CASE_KEYS = {*FIELDS, "judge_correctness", "judge_completeness", "judge_citation_support",
             "judge_unsupported_claims"}

# (case id, extra tags, answered right) — every case is split:dev
RUN = [
    ("a", ["intent:i1", "family:terse", "key_source:sec_companyfacts"], True),
    ("b", ["intent:i1", "family:formal", "key_source:sec_companyfacts"], False),
    ("c", ["intent:i2", "family:terse", "key_source:html_hand"], True),
    ("d", ["intent:i2", "family:formal", "key_source:html_hand"], True),
    ("e", ["intent:i3", "family:terse", "key_source:html_hand"], False),
    ("f", ["intent:i3", "family:conversational"], False),
    ("g", ["intent:i4", "family:terse"], True),
    ("h", [], True),
]


def _results(make_case, graded, rows=RUN):
    return [graded(make_case(cid, ["split:dev", *tags]), ok) for cid, tags, ok in rows]


def _sizes_and_accuracy(metrics_by_key):
    return {k: (m.n, m.accuracy) for k, m in metrics_by_key.items()}


@pytest.mark.criterion("C8")
def test_slice_metrics_by_family_and_key_source(make_case, graded):
    # oracle: hand count over RUN — terse a✓ c✓ e✗ g✓; formal b✗ d✓; conversational f✗;
    # untagged h✓. sec_companyfacts a✓ b✗; html_hand c✓ d✓ e✗; untagged f✗ g✓ h✓
    results = _results(make_case, graded)
    fam = slice_metrics(results, "phrasing_family")
    assert list(fam) == sorted(fam)
    got = _sizes_and_accuracy(fam)
    assert set(got) == {"terse", "formal", "conversational", "(none)"}
    # tolerance: exact small ratios; abs=1e-12 only absorbs float division
    assert got["terse"][0] == 4 and got["terse"][1] == pytest.approx(3 / 4, abs=1e-12)
    assert got["formal"][0] == 2 and got["formal"][1] == pytest.approx(1 / 2, abs=1e-12)
    assert got["conversational"][0] == 1
    assert got["conversational"][1] == pytest.approx(0.0, abs=1e-12)
    assert got["(none)"][0] == 1 and got["(none)"][1] == pytest.approx(1.0, abs=1e-12)
    src = _sizes_and_accuracy(slice_metrics(results, "key_source"))
    assert set(src) == {"sec_companyfacts", "html_hand", "(none)"}
    assert src["sec_companyfacts"][0] == 2
    assert src["sec_companyfacts"][1] == pytest.approx(1 / 2, abs=1e-12)
    assert src["html_hand"][0] == 3
    assert src["html_hand"][1] == pytest.approx(2 / 3, abs=1e-12)
    assert src["(none)"][0] == 3 and src["(none)"][1] == pytest.approx(2 / 3, abs=1e-12)
    split = _sizes_and_accuracy(slice_metrics(results, "split"))
    assert set(split) == {"dev"}
    assert split["dev"][0] == 8 and split["dev"][1] == pytest.approx(5 / 8, abs=1e-12)


@pytest.mark.criterion("C8")
def test_slice_metrics_rejects_unknown_field(make_case, graded):
    # oracle: requirement 10 — only the six tag fields can be sliced on
    with pytest.raises(ValueError):
        slice_metrics(_results(make_case, graded), "kind")


@pytest.mark.criterion("C8")
def test_intent_stability_hand_count(make_case, graded):
    # oracle: hand count over RUN — i1 mixed, i2 all right, i3 all wrong (stable),
    # i4 has one case and h none, both ignored: 2 stable of 3
    # tolerance: an exact ratio 2/3; abs=1e-12 only absorbs float division
    assert intent_stability(_results(make_case, graded)) == pytest.approx(2 / 3, abs=1e-12)
    single = [("p", ["intent:only"], True), ("q", [], False), ("r", [], True)]
    assert intent_stability(_results(make_case, graded, single)) is None


@pytest.mark.criterion("C8")
def test_slice_report_shape_and_values(make_case, graded):
    # oracle: requirement 10 with the RUN hand counts above; the by_* values are
    # Metrics.to_dict(), which rounds to 4 places
    rep = slice_report(_results(make_case, graded))
    assert set(rep) == {"by_split", "by_phrasing_family", "by_key_source",
                        "intent_stability"}
    assert set(rep["by_split"]) == {"dev"}
    assert set(rep["by_phrasing_family"]) == {"terse", "formal", "conversational", "(none)"}
    assert set(rep["by_key_source"]) == {"sec_companyfacts", "html_hand", "(none)"}
    assert rep["by_phrasing_family"]["formal"]["n"] == 2
    # tolerance: Metrics.to_dict rounds to 4 places
    assert rep["by_phrasing_family"]["formal"]["accuracy"] == pytest.approx(0.5, abs=1e-4)
    assert rep["by_key_source"]["(none)"]["n"] == 3
    assert rep["by_key_source"]["(none)"]["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert rep["intent_stability"] == pytest.approx(2 / 3, abs=1e-4)


@pytest.mark.criterion("C8")
def test_report_to_dict_carries_slices_and_columns(make_case, graded, make_report):
    # oracle: requirement 9 with the RUN hand counts above; to_dict rounds metrics to 4
    # places, so the slice accuracies are compared at abs=1e-4
    report = make_report(_results(make_case, graded), config={"split": "dev"})
    d = report.to_dict()
    assert d.get("split") == "dev"
    assert "judge_version" in d and d["judge_version"] is None
    slices = d.get("slices") or {}
    assert set(slices) == {"by_split", "by_phrasing_family", "by_key_source",
                           "intent_stability"}
    # tolerance: to_dict rounds to 4 places
    assert slices["by_split"]["dev"]["accuracy"] == pytest.approx(5 / 8, abs=1e-4)
    assert slices["by_split"]["dev"]["n"] == 8
    assert slices["by_phrasing_family"]["terse"]["accuracy"] == pytest.approx(0.75, abs=1e-4)
    assert slices["by_key_source"]["html_hand"]["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert slices["intent_stability"] == pytest.approx(2 / 3, abs=1e-4)
    # tolerance: to_dict may round to 4 places
    assert d.get("paraphrase_stability") == pytest.approx(2 / 3, abs=1e-4)
    by_id = {r["case_id"]: r for r in d["results"]}
    assert CASE_KEYS <= set(by_id["a"])
    assert by_id["a"]["intent_id"] == "i1"
    assert by_id["a"]["key_source"] == "sec_companyfacts"
    assert by_id["h"]["phrasing_family"] is None


INTENT = st.one_of(st.none(), st.sampled_from(["i1", "i2", "i3", "i4", "i5"]))


@pytest.mark.criterion("C8")
@given(rows=st.lists(st.tuples(INTENT, st.booleans()), min_size=0, max_size=25),
       seed=st.integers(0, 2**32 - 1))
def test_intent_stability_closed_form_and_order_free(make_case, graded, rows, seed):
    # oracle: requirement 10's definition computed here — group by intent, keep groups
    # of 2+, count the ones whose outcomes agree; None with no kept group
    groups: dict[str, list[bool]] = {}
    for intent, ok in rows:
        if intent is not None:
            groups.setdefault(intent, []).append(ok)
    kept = [v for v in groups.values() if len(v) >= 2]
    spec_rows = [(f"c{i}", [f"intent:{it}"] if it else [], ok)
                 for i, (it, ok) in enumerate(rows)]
    results = _results(make_case, graded, spec_rows)
    shuffled = list(results)
    random.Random(seed).shuffle(shuffled)
    if not kept:
        assert intent_stability(results) is None
        assert intent_stability(shuffled) is None
        assert slice_metrics(results, "intent_id") == slice_metrics(shuffled, "intent_id")
        assert sum(m.n for m in slice_metrics(results, "split").values()) == len(rows)
        return
    expected = sum(1 for v in kept if len(set(v)) == 1) / len(kept)
    # tolerance: exact rationals; abs=1e-12 only absorbs float division
    assert intent_stability(results) == pytest.approx(expected, abs=1e-12)
    assert intent_stability(shuffled) == pytest.approx(expected, abs=1e-12)


@pytest.mark.criterion("C8")
@given(rows=st.lists(st.tuples(st.sampled_from(["terse", "formal", None]), st.booleans()),
                     min_size=1, max_size=20),
       field=st.sampled_from(FIELDS))
def test_slice_bucket_sizes_sum_to_n(make_case, graded, rows, field):
    # oracle: requirement 10 — every result lands in exactly one bucket (untagged under
    # "(none)"), so the bucket sizes sum to the number of results; and the family
    # buckets' correct counts match the count made here
    spec_rows = [(f"c{i}", [f"family:{f}"] if f else [], ok) for i, (f, ok) in enumerate(rows)]
    results = _results(make_case, graded, spec_rows)
    buckets = slice_metrics(results, field)
    assert sum(m.n for m in buckets.values()) == len(rows)
    fam = slice_metrics(results, "phrasing_family")
    for key in {f or "(none)" for f, _ in rows}:
        n_right = sum(1 for f, ok in rows if (f or "(none)") == key and ok)
        assert fam[key].n_correct == n_right
