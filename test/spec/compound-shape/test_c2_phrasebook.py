"""C2: compound templates load under requirement 2's placeholder table, render by exact
substitution, and the committed compound phrasebook and lexicon cover both shapes.

Oracle: the requirement 2 table (restated below as FILL) and question texts written by hand
from the fixture phrasebook and lexicon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Phrasing, load_lexicon, load_phrasebook, render

# Requirement 2: shape -> (fillable, required).
FILL = {
    "compound.rank_by_ratio": ({"metric", "metric_2", "period", "universe", "n"},) * 2,
    "compound.leader_growth": (
        {"metric", "metric_2", "first_period", "last_period", "universe", "n"},
        {"metric", "metric_2", "last_period", "universe", "n"}),
}
REGISTERS = {"terse", "conversational", "formal"}


def _write(tmp_path, name, templates):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"templates": templates}, sort_keys=False), encoding="utf-8")
    return path


def _t(tid, shape, text, register="terse"):
    return {"id": tid, "register": register, "shapes": [shape], "text": text}


GOOD_RBR = "top {n} {universe} by {metric} {period}: highest {metric_2}?"
GOOD_LG = "top {n} {universe} by {metric} {last_period}: leader {metric_2} growth"
GOOD_LG_SPAN = "{n} {universe} {metric} {last_period}: {metric_2} growth from {first_period}"


@pytest.mark.criterion("C2")
def test_loader_accepts_the_table_and_rejects_its_violations(kit, tmp_path):
    # oracle: requirement 2 table — company form none ({company} never fillable), every
    # required placeholder used, nothing outside the fillable set; {first_period} optional
    ok = kit.compound_phrasebook(_write(tmp_path, "ok.yaml", [
        _t("r1", "compound.rank_by_ratio", GOOD_RBR),
        _t("g1", "compound.leader_growth", GOOD_LG),
        _t("g2", "compound.leader_growth", GOOD_LG_SPAN),
    ]))
    assert [t.id for t in ok.templates] == ["r1", "g1", "g2"]
    bad = [
        _t("b1", "compound.rank_by_ratio", "{company}: " + GOOD_RBR),
        _t("b2", "compound.leader_growth", "{company}: " + GOOD_LG),
        _t("b3", "compound.rank_by_ratio", "top {universe} by {metric} {period}: {metric_2}"),
        _t("b4", "compound.rank_by_ratio", "top {n} {universe} by {metric} {period}"),
        _t("b5", "compound.leader_growth", "top {n} {universe} by {metric}: {metric_2}"),
        _t("b6", "compound.leader_growth", GOOD_LG + " in {period}"),
        _t("b7", "compound.rank_by_ratio", GOOD_RBR + " since {first_period}"),
        _t("b8", "compound.leader_growth", GOOD_LG + " by {dimension}"),
    ]
    for i, template in enumerate(bad):
        with pytest.raises(ValueError):
            load_phrasebook(_write(tmp_path, f"bad{i}.yaml", [template]))


def _rbr_intent(kit):
    return kit.make_intent(kit.rbr(2024, "Revenue", "operating_margin"))


def _lg_intent(kit):
    return kit.make_intent(kit.lg(2024, "Revenue", "NetIncome"))


@pytest.mark.criterion("C2")
def test_render_rank_by_ratio(kit):
    # oracle: the fixture template rbr-conv with the lexicon's surfaces substituted by hand
    pb, lex = kit.compound_phrasebook(), kit.lexicon()
    intent = _rbr_intent(kit)
    got = render(intent, Phrasing("rbr-conv", company_form="none"), lex, pb)
    assert got == ("Of the 3 largest of the covered companies by revenue in fiscal 2024, "
                   "which had the highest operating margin?")
    got = render(intent, Phrasing("rbr-terse", company_form="none", metric_form="synonym:0",
                                  period_form="fy_short"), lex, pb)
    assert got == "top 3 the covered companies by net sales FY24: highest operating profit margin?"


@pytest.mark.criterion("C2")
def test_render_leader_growth(kit):
    # oracle: the fixture template lg-conv: {last_period} the ranking year 2024,
    # {first_period} the growth base 2023
    pb, lex = kit.compound_phrasebook(), kit.lexicon()
    got = render(_lg_intent(kit), Phrasing("lg-conv", company_form="none",
                                           period_form="fiscal_year"), lex, pb)
    assert got == ("Of the 3 largest of the covered companies by revenue in fiscal year 2024, "
                   "how much did the largest one's net income grow from fiscal year 2023?")
    with pytest.raises(ValueError):  # a compound intent has no company to render
        render(_lg_intent(kit), Phrasing("lg-conv", company_form="ticker"), lex, pb)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(form=st.sampled_from(["label", "synonym:0"]),
       period=st.sampled_from(["fy_short", "fiscal", "fiscal_year"]),
       ratio=st.sampled_from(["operating_margin", "net_margin", "rd_intensity",
                              "current_ratio"]),
       tid=st.sampled_from(["rbr-terse", "rbr-conv", "rbr-formal"]))
def test_render_is_exact_substitution(kit, form, period, ratio, tid):
    # oracle: render substitutes each placeholder by its surface (hand-restated from the raw
    # fixture YAML), including a ratio id's lexicon surface as {metric_2}
    raw_pb = yaml.safe_load((kit.HERE / "phrasebook_compound.yaml").read_text("utf-8"))
    raw_lex = yaml.safe_load((kit.HERE / "lexicon.yaml").read_text("utf-8"))
    text = next(t["text"] for t in raw_pb["templates"] if t["id"] == tid)

    def surface(metric):
        entry = raw_lex["metrics"][metric]
        return entry["label"] if form == "label" else entry["synonyms"][0]

    period_text = {"fy_short": "FY24", "fiscal": "fiscal 2024",
                   "fiscal_year": "fiscal year 2024"}[period]
    want = (text.replace("{n}", "3").replace("{universe}", raw_lex["universes"]["big"][0])
            .replace("{metric_2}", surface(ratio)).replace("{metric}", surface("Revenue"))
            .replace("{period}", period_text))
    intent = kit.make_intent(kit.rbr(2024, "Revenue", ratio))
    got = render(intent, Phrasing(tid, company_form="none", metric_form=form,
                                  period_form=period), kit.lexicon(), kit.compound_phrasebook())
    assert got == want


def _committed():
    base = Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2"
    return base / "phrasebook_compound.yaml", base / "lexicon.yaml"


@pytest.mark.criterion("C2")
def test_committed_compound_phrasebook_and_lexicon(kit):
    # oracle: requirement 2 — at least three templates per compound shape, all three
    # registers per shape, a lexicon label for each of the four ratio ids
    pb_path, lex_path = _committed()
    assert pb_path.is_file(), f"{pb_path} is not committed"
    pb = kit.compound_phrasebook(pb_path)
    for shape in FILL:
        mine = [t for t in pb.templates if shape in t.shapes]
        assert len(mine) >= 3, shape
        assert {t.register for t in mine} == REGISTERS, shape
    lex = load_lexicon(lex_path)
    for ratio in kit.RATIO_IDS:
        assert ratio in lex.metrics, ratio
        assert lex.metrics[ratio]["label"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_committed_templates_respect_the_table(kit):
    # oracle: requirement 2 table, checked on the raw YAML independently of the loader; the
    # loader's view agrees with the raw file
    pb_path, _lex = _committed()
    assert pb_path.is_file(), f"{pb_path} is not committed"
    raw = yaml.safe_load(pb_path.read_text(encoding="utf-8"))["templates"]
    pb = kit.compound_phrasebook(pb_path)
    assert [(t.id, tuple(t.shapes), t.text) for t in pb.templates] == [
        (r["id"], tuple(r["shapes"]), r["text"]) for r in raw]
    for r in raw:
        used = set(re.findall(r"\{([a-z_0-9]+)\}", r["text"]))
        for shape in r["shapes"]:
            fillable, required = FILL[shape]
            assert required <= used <= fillable, (r["id"], shape)
