"""C4: ``unmatched_numbers`` applies the matching rules to a statement's cited evidence (rule 3).

Evidence is apple_evidence(): real FY2024 facts (see the spec's Notes). Each expected list is
hand arithmetic, written next to the case.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unmatched_numbers

REL = 1e-12  # values are decimal text times a power of ten

# AAPL FY2024: revenue 391,035 m (decimals -6), net income 93,736 m, gross profit 180,683 m,
# diluted EPS 6.08; net margin m_aapl = 93,736 / 391,035 = 0.2397125...
MARGIN = 93_736 / 391_035


def _unmatched(ci, statement, fact_ids, chunk_ids=()):
    steps, plan_steps, chunks = ci.apple_evidence()
    return unmatched_numbers(ci.detail(statement, list(fact_ids), list(chunk_ids)),
                             {"status": "OK", "steps": steps}, {"steps": plan_steps}, chunks)


def _values(found):
    return [n["value"] for n in found]


CASES = [
    # --- a reported amount: revenue 391,035,000,000, decimals -6 (u_rule = 1e6)
    ("Revenue was $391.0 billion.", ["13253"], [], []),           # |391.0e9-v| = 3.5e7 <= 5e7
    ("Revenue was $391,035 million.", ["13253"], [], []),         # exact
    ("Revenue was $391 billion.", ["13253"], [], []),             # 3.5e7 <= 5e8: coarser, kept
    ("Revenue was $0.4 trillion.", ["13253"], [], []),            # 8.965e9 <= 5e10
    ("Revenue was $391,035,400,000.", ["13253"], [], []),         # 4e5 <= max(1, 1e6)/2
    ("Revenue was $391,036 million.", ["13253"], [], [391_036e6]),  # 1e6 > 5e5
    ("Revenue was $391.1 billion.", ["13253"], [], [391.1e9]),    # 6.5e7 > 5e7
    ("Revenue was $245.1 billion.", ["13253"], [], [245.1e9]),    # Microsoft's figure
    ("Revenue was $391.0 billion.", ["r_aapl"], [], []),          # cited by step id
    # --- per share: 6.08 (u_rule = 0.01)
    ("Diluted EPS was $6.08.", ["e_aapl"], [], []),
    ("Diluted EPS was $6.1.", ["e_aapl"], [], []),                # 0.02 <= 0.05
    ("Diluted EPS was $6.081.", ["e_aapl"], [], []),              # 0.001 <= max(.001,.01)/2
    ("Diluted EPS was $6.09.", ["e_aapl"], [], [6.09]),           # 0.01 > 0.005
    # --- a ratio: m_aapl = 0.2397125 (3 s.f.: u_rule = 0.001; percent: 0.1 pp)
    ("Net margin was 24.0%.", ["m_aapl"], [], []),                # |24.0-23.971| = 0.029 <= .05
    ("Net margin was 23.97%.", ["m_aapl"], [], []),
    ("Net margin was 0.240.", ["m_aapl"], [], []),                # 0.00029 <= 0.0005
    ("Net margin was 0.2398.", ["m_aapl"], [], []),               # 0.000087 <= 0.0005
    ("Net margin was 24.1%.", ["m_aapl"], [], [24.1]),            # 0.129 > 0.05
    ("Net margin was 0.241.", ["m_aapl"], [], [0.241]),           # 0.0013 > 0.0005
    # --- a compute's inputs count as cited: n_aapl and r_aapl are in m_aapl's expr
    ("Net margin was 24.0% on net income of $93.7 billion and revenue of $391.0 billion.",
     ["m_aapl"], [], []),
    ("Net margin was 24.0% on gross profit of $180.7 billion.",   # g_aapl is not an input
     ["m_aapl"], [], [180.7e9]),
    # --- screen rows, cited per filer
    ("Amazon's revenue was $638.0 billion.", ["6854"], [], []),   # 4.1e7 <= 5e7
    ("Walmart's revenue was $637,959 million.", ["205006"], [], [637_959e6]),  # Amazon's value
    ("China was 17.1% of revenue, or $67.0 billion.", ["14256"], [], []),  # 0.022 pp; 4.8e7
    ("China was 17.3% of revenue.", ["14256"], [], [17.3]),       # 0.178 > 0.05
    # --- breakdown rows: iPhone 201,183 m, Services 96,169 m
    ("iPhone revenue was $201.2 billion and Services $96.2 billion.",
     ["13550", "13562"], [], []),
    ("iPhone revenue was $211.2 billion.", ["13550", "13562"], [], [211.2e9]),
    # --- a derived lookup: gross profit 180,683 m
    ("Gross profit was $180.7 billion.", ["gp_der"], [], []),
    # --- chunk text: "Services gross margin percentage was 73.9% in 2024."
    ("Services gross margin was 73.9%.", [], ["CHUNK"], []),
    ("Services gross margin was 74.9%.", [], ["CHUNK"], [74.9]),
    ("Revenue was $391.0 billion and Services margin 73.9%.", ["13253"], ["CHUNK"], []),
    # --- the synthesizer's own arithmetic: 391,035 / 383,285 - 1 = 2.02 % is in no evidence
    ("Revenue grew 2.0% from fiscal 2023.", ["13253"], [], [2.0]),
    # --- only exempt numbers
    ("In fiscal 2024 Apple ranked 3rd among 5 companies.", ["13253"], [], []),
]


@pytest.mark.criterion("C4")
def test_matching_rules_on_real_values(ci):
    # oracle: hand arithmetic per case (comments in CASES) under the spec's matching table
    for statement, fact_ids, chunk_ids, expected in CASES:
        chunk_ids = [ci.CHUNK_AAPL_MDNA if c == "CHUNK" else c for c in chunk_ids]
        found = _unmatched(ci, statement, fact_ids, chunk_ids)
        assert _values(found) == pytest.approx(expected, rel=REL), statement
        assert all(n["text"] in statement for n in found)


@pytest.mark.criterion("C4")
def test_a_value_matches_only_through_a_cited_step(ci):
    # oracle: rule 3 — the evidence is what the statement cites: Apple's revenue is in the
    # execution result, but a statement citing only Apple's EPS does not cite it
    assert _values(_unmatched(ci, "Revenue was $391.0 billion.", ["e_aapl"])) == \
        pytest.approx([391.0e9], rel=REL)
    assert _values(_unmatched(ci, "Revenue was $391.0 billion.", ["e_aapl", "r_aapl"])) == []


_AMOUNTS = [("AAPL", "Revenue"), ("AAPL", "NetIncome"), ("AAPL", "GrossProfit")]
_STEP_OF = {("AAPL", "Revenue"): "r_aapl", ("AAPL", "NetIncome"): "n_aapl",
            ("AAPL", "GrossProfit"): "g_aapl"}


@pytest.mark.criterion("C4")
@given(key=st.sampled_from(_AMOUNTS),
       scale=st.sampled_from([("million", 1e6, 2), ("billion", 1e9, 3), ("trillion", 1e12, 4)]),
       data=st.data())
def test_restating_in_any_scale_matches_and_two_units_off_does_not(ci, key, scale, data):
    # oracle: a whole-million fact (decimals -6) rounded to d decimals of the scale is within
    # half a stated step; moving it by 2 x max(stated step, 1e6) breaks the match
    word, mult, max_d = scale
    d = data.draw(st.integers(0, max_d))
    value = ci.fact(*key)[1]
    shown = round(value / mult, d)
    bump = 2 * max(10.0 ** -d, 1e6 / mult)
    good = f"It was ${shown:,.{d}f} {word}."
    bad = f"It was ${shown + bump:,.{d}f} {word}."
    assert _unmatched(ci, good, [_STEP_OF[key]]) == []
    found = _unmatched(ci, bad, [_STEP_OF[key]])
    assert _values(found) == pytest.approx([(shown + bump) * mult], rel=1e-9)


@pytest.mark.criterion("C4")
@given(d=st.integers(0, 3), shift=st.sampled_from([-0.3, 0.3, -1.0, 1.0]))
def test_a_percent_matches_at_a_tenth_of_a_point(ci, d, shift):
    # oracle: 100 x m_aapl = 23.97125; rounded to d decimals it is within max(10^-d, 0.1)/2;
    # 0.3 pp off is beyond 0.05 + 0.0005 for d >= 1, and 1 pp off is beyond 0.5 for d = 0
    pct = round(100 * MARGIN, d)
    assert _unmatched(ci, f"Net margin was {pct:.{d}f}%.", ["m_aapl"]) == []
    if d == 0 and abs(shift) < 1:
        shift = 1.0 if shift > 0 else -1.0
    found = _unmatched(ci, f"Net margin was {pct + shift:.{d}f}%.", ["m_aapl"])
    assert _values(found) == pytest.approx([round(pct + shift, d)], rel=1e-9)
