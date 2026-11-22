"""Spec tests C9-C11 for eval-intents: the two-axis hash split and the phrased-case builder
(docs/specs/eval-intents.md)."""

from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import (
    Intent,
    Phrasing,
    axis_is_validation,
    build_phrased_cases,
    intent_id,
    load_lexicon,
    load_phrasebook,
    render,
)

KEYS = [f"k{i}" for i in range(40)] + ["lookup-conv", "int-0123456789ab", ""]


def _load(kit):
    return load_phrasebook(kit.phrasebook), load_lexicon(kit.lexicon)


def _build(intents, pb, lex, per_intent, share=0.4, salt="s"):
    return build_phrased_cases(list(intents), pb, lex, per_intent=per_intent, share=share,
                               salt=salt)


def _intent_kw(ticker, year, metric):
    return dict(kind="lookup", tickers=[ticker], fiscal_years=[year], metrics=[metric])


def _screen_kw(year, metric, op):
    n = 3 if op in ("top", "bottom") else None
    return dict(kind="screen", operation=op, universe="covered", n=n, fiscal_years=[year],
                metrics=[metric])


def _trend_kw(ticker, year):
    return dict(kind="trend", tickers=[ticker], fiscal_years=[year, year + 1, year + 2],
                metrics=["revenue"])


INTENT_KWS = st.one_of(
    st.builds(_intent_kw, st.sampled_from(["AAPL", "MSFT", "NVDA"]), st.integers(2015, 2030),
              st.sampled_from(["revenue", "operating_income", "gross_profit", "rd_expense"])),
    st.builds(_screen_kw, st.integers(2015, 2030), st.sampled_from(["revenue", "gross_profit"]),
              st.sampled_from(["top", "bottom", "sum", "median"])),
    st.builds(_trend_kw, st.sampled_from(["AAPL", "MSFT"]), st.integers(2018, 2022)),
)


def _unique(kws, kit):
    seen, out = set(), []
    for kw in kws:
        key = kit.oracle_id(kw)
        if key not in seen:
            seen.add(key)
            out.append(kw)
    return out


def _n_candidates(kit, kws, per_intent):
    templates, raw = kit.raw_phrasebook(), kit.raw_lexicon()
    return sum(min(per_intent, len(kit.candidates(kw, templates, raw))) for kw in kws)


def _view(cases):
    return [(c.case_id, c.question, c.template_id, c.register,
             tuple(sorted(dict(c.forms).items())), c.split, c.intent_axis, c.template_axis,
             intent_id(c.intent)) for c in cases]


# --------------------------------------------------------------------------- C9
@pytest.mark.criterion("C9")
def test_c9_axis_point(kit):
    # oracle: the C9 rule computed with hashlib; the sample must hold both outcomes at share 0.5.
    want = [kit.oracle_axis(k, 0.5, "v2") for k in KEYS]
    assert any(want) and not all(want)
    assert [axis_is_validation(k, share=0.5, salt="v2") for k in KEYS] == want
    assert all(axis_is_validation(k, share=1.0, salt="v2") is True for k in KEYS)
    assert all(axis_is_validation(k, share=0.0, salt="v2") is False for k in KEYS)
    h = int(hashlib.sha1(b"s:k0").hexdigest()[:8], 16) / 2**32
    assert axis_is_validation("k0", share=h, salt="s") is False       # strict "<"
    assert axis_is_validation("k0", share=min(1.0, h + 1e-9), salt="s") is True
    for bad in (-0.01, 1.01, float("nan")):
        with pytest.raises(ValueError):
            axis_is_validation("k0", share=bad, salt="s")


@pytest.mark.criterion("C9")
@given(key=st.text(max_size=30), salt=st.text(max_size=10),
       s1=st.floats(min_value=0.0, max_value=1.0), s2=st.floats(min_value=0.0, max_value=1.0))
def test_c9_axis_property(kit, key, salt, s1, s2):
    # oracle: the C9 rule; monotone in share.
    lo, hi = min(s1, s2), max(s1, s2)
    a = axis_is_validation(key, share=lo, salt=salt)
    b = axis_is_validation(key, share=hi, salt=salt)
    assert a == kit.oracle_axis(key, lo, salt)
    assert b == kit.oracle_axis(key, hi, salt)
    assert (not a) or b
    assert axis_is_validation(key, share=1.0, salt=salt) is True


@pytest.mark.criterion("C9")
@given(share=st.floats(min_value=0.05, max_value=0.95),
       salt=st.sampled_from(["a", "b", "v2-2026"]))
def test_c9_split_invariant_property(kit, share, salt):
    # oracle: C9 — each axis is the rule over its own key; split is validation iff either axis
    # is; a dev case never carries a validation intent or template.
    pb, lex = _load(kit)
    kws = list(kit.examples.values())
    cases = _build([Intent(**kw) for kw in kws], pb, lex, 6, share=share, salt=salt)
    assert len(cases) == _n_candidates(kit, kws, 6)
    val_intents = {intent_id(c.intent) for c in cases if c.intent_axis == "validation"}
    val_templates = {c.template_id for c in cases if c.template_axis == "validation"}
    for c in cases:
        ia = "validation" if kit.oracle_axis(intent_id(c.intent), share, salt) else "dev"
        ta = "validation" if kit.oracle_axis(c.template_id, share, salt) else "dev"
        assert (c.intent_axis, c.template_axis) == (ia, ta)
        assert c.split == ("validation" if "validation" in (ia, ta) else "dev")
        if c.split == "dev":
            assert intent_id(c.intent) not in val_intents
            assert c.template_id not in val_templates


@pytest.mark.criterion("C9")
def test_c9_split_point(kit):
    # oracle: shares 0 and 1 put every case in dev and validation respectively (C9 rule bounds).
    pb, lex = _load(kit)
    intents = [Intent(**kit.examples["lookup"]), Intent(**kit.examples["screen.top"])]
    dev = _build(intents, pb, lex, 4, share=0.0, salt="x")
    val = _build(intents, pb, lex, 4, share=1.0, salt="x")
    assert len(dev) == len(val) == 8
    assert {(c.split, c.intent_axis, c.template_axis) for c in dev} == {("dev", "dev", "dev")}
    assert {(c.split, c.intent_axis, c.template_axis) for c in val} == {("validation",) * 3}


# --------------------------------------------------------------------------- C10
@pytest.mark.criterion("C10")
def test_c10_selection_point(kit):
    # oracle: the candidate enumeration and SHA-1 selection written out in conftest (independent
    # of the code under test), plus hand counts of the candidate sets.
    pb, lex = _load(kit)
    templates, raw = kit.raw_phrasebook(), kit.raw_lexicon()
    # Hand counts: AAPL lookup = 3 templates x 3 company x 4 metric (label, 2 synonyms,
    # 1 shorthand) x 4 period (3 + year_ended); NVDA has no period ends: 3 x 3 x 4 x 3;
    # screen.top = 1 x 1 x 4 x 3.
    assert len(kit.candidates(kit.examples["lookup"], templates, raw)) == 144
    nvda = _intent_kw("NVDA", 2024, "revenue")
    assert len(kit.candidates(nvda, templates, raw)) == 108
    assert len(kit.candidates(kit.examples["screen.top"], templates, raw)) == 12
    every = _build([Intent(**kit.examples["lookup"])], pb, lex, 1000, share=0.3)
    assert len(every) == 144
    kws = [kit.examples["lookup"], nvda, kit.examples["screen.top"], kit.examples["compute.ratio"]]
    cases = _build([Intent(**kw) for kw in kws], pb, lex, 5, share=0.3)
    want = {}
    for kw in kws:
        want.update(kit.expected_selection(kw, 5, templates, raw))
    assert [c.case_id for c in cases] == sorted(want)
    registers = {t["id"]: t["register"] for t in templates}
    for c in cases:
        tid, cf, mf, pf = want[c.case_id]
        assert (c.template_id, dict(c.forms)) == (tid, {"company": cf, "metric": mf, "period": pf})
        assert c.register == registers[tid]
        phrasing = Phrasing(tid, company_form=cf, metric_form=mf, period_form=pf)
        assert c.question == render(c.intent, phrasing, lex, pb)
        assert c.case_id.startswith(intent_id(c.intent) + "-")
        assert len(c.case_id) == 16 + 1 + 8


@pytest.mark.criterion("C10")
def test_c10_errors_point(kit, tmp_path):
    # oracle: C10 — per_intent < 1, a duplicate intent and an unphraseable intent raise.
    pb, lex = _load(kit)
    a = Intent(**kit.examples["lookup"])
    assert len(_build([a], pb, lex, 1)) == 1
    for bad in (0, -3):
        with pytest.raises(ValueError):
            _build([a], pb, lex, bad)
    with pytest.raises(ValueError):
        _build([a, Intent(**copy.deepcopy(kit.examples["lookup"]))], pb, lex, 2)
    template = {"id": "t", "register": "terse", "shapes": ["lookup"],
                "text": "{company} {metric} {period}"}
    only_lookup = load_phrasebook(kit.write_yaml(tmp_path, "pb.yaml", {"templates": [template]}))
    assert len(_build([a], only_lookup, lex, 2)) == 2
    with pytest.raises(ValueError):
        _build([a, Intent(**kit.examples["trend"])], only_lookup, lex, 2)


@pytest.mark.criterion("C10")
@given(kws=st.lists(INTENT_KWS, min_size=1, max_size=5), per_intent=st.integers(1, 12))
def test_c10_selection_property(kit, kws, per_intent):
    # oracle: kit.expected_selection, the spec's enumeration and selection restated in conftest.
    kws = _unique(kws, kit)
    pb, lex = _load(kit)
    templates, raw = kit.raw_phrasebook(), kit.raw_lexicon()
    cases = _build([Intent(**kw) for kw in kws], pb, lex, per_intent, share=0.4, salt="p")
    want = {}
    for kw in kws:
        want.update(kit.expected_selection(kw, per_intent, templates, raw))
    assert [c.case_id for c in cases] == sorted(want)
    for c in cases:
        tid, cf, mf, pf = want[c.case_id]
        assert (c.template_id, dict(c.forms)) == (tid, {"company": cf, "metric": mf, "period": pf})


# --------------------------------------------------------------------------- C11
@pytest.mark.criterion("C11")
def test_c11_stability_point(kit):
    # oracle: C11 — the selection depends only on each intent's own id and the phrasebook.
    pb, lex = _load(kit)
    kws = list(kit.examples.values())
    intents = [Intent(**kw) for kw in kws]
    base = _view(_build(intents, pb, lex, 3))
    assert len(base) == 3 * len(intents)
    assert _view(_build(intents, pb, lex, 3)) == base
    assert _view(_build(intents[::-1], pb, lex, 3)) == base
    extra = [Intent(**_intent_kw("NVDA", 2020, "revenue")),
             Intent(**_intent_kw("MSFT", 2021, "rd_expense"))]
    grown = _view(_build(intents + extra, pb, lex, 3))
    assert len(grown) == len(base) + 6 and set(base) <= set(grown)
    wider = _view(_build(intents, pb, lex, 5))
    assert set(base) <= set(wider)
    assert len(wider) == _n_candidates(kit, kws, 5)
    assert len(wider) > len(base)
    resplit = _build(intents, pb, lex, 3, share=0.9, salt="other")
    assert [(c.case_id, c.question) for c in resplit] == [(v[0], v[1]) for v in base]


@pytest.mark.criterion("C11")
@given(kws=st.lists(INTENT_KWS, min_size=2, max_size=6), per_intent=st.integers(1, 6),
       data=st.data())
def test_c11_order_and_additivity_property(kit, kws, per_intent, data):
    # oracle: C11 — permuting the intents changes nothing; a subset's cases are exactly the
    # full set's cases for those intents.
    kws = _unique(kws, kit)
    pb, lex = _load(kit)
    intents = [Intent(**kw) for kw in kws]
    full = _view(_build(intents, pb, lex, per_intent, share=0.5, salt="m"))
    assert full
    perm = list(data.draw(st.permutations(intents)))
    assert _view(_build(perm, pb, lex, per_intent, share=0.5, salt="m")) == full
    sub = intents[: max(1, len(intents) // 2)]
    sub_ids = {intent_id(i) for i in sub}
    part = _view(_build(sub, pb, lex, per_intent, share=0.5, salt="m"))
    assert part == [v for v in full if v[-1] in sub_ids]
