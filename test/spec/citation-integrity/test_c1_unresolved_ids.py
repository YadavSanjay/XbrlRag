"""C1: ``unresolved_citation_ids`` names exactly the cited ids that resolve to nothing (rule 1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unresolved_citation_ids

# Every id that resolves in apple_evidence(), by the spec's rule 1 (hand-listed from the
# fixture's docstring): step ids of non-narrative steps, lookup citation fact ids, screen and
# breakdown row fact ids, component fact ids.
RESOLVING_FACT_IDS = [
    "r_aapl", "n_aapl", "m_aapl", "g_aapl", "e_aapl", "gp_der", "r_msft", "s1", "s2", "b1",
    "13253", "13289", "13265", "13295",             # lookup citations
    "205006", "6854", "14256", "13550", "13562",    # screen and breakdown rows (13253 again)
    "13262",                                        # gp_der's cost-of-revenue component
]


def _evidence(ci):
    steps, _, chunks = ci.apple_evidence()
    return {"status": "OK", "steps": steps}, chunks


@pytest.mark.criterion("C1")
def test_each_kind_of_id_resolves_or_is_named(ci):
    # oracle: rule 1 of docs/specs/citation-integrity.md applied by hand to apple_evidence()
    er, chunks = _evidence(ci)
    cases = [
        # (fact_ids, chunk_ids, expected unresolved)
        ([13253], [], []),                                   # an int id compares as a string
        ([13253, 9000000001], [], ["9000000001"]),           # a real fact plus an invented id
        (["m_aapl", "r_aapl", "s1", "b1"], [], []),          # compute, lookup, screen, breakdown
        (["6854", "13550"], [], []),                         # screen row, breakdown row
        (["13262", "gp_der"], [], []),                       # component fact, derived step
        (["r_msft"], [], []),                                # a NOT_FOUND step still resolves
        (["n1"], [], ["n1"]),                                # a narrative step is no fact
        ([], [ci.CHUNK_AAPL_MDNA], []),                      # a retrieved chunk
        ([], [ci.LINKED_ONLY_CHUNK], [ci.LINKED_ONLY_CHUNK]),  # linked to a step, not retrieved
        ([], [ci.CHUNK_AMZN_MDNA], [ci.CHUNK_AMZN_MDNA]),    # retrieved by no step of this query
        (["x1", "13253", "f1"], ["bogus", ci.CHUNK_AAPL_MDNA], ["x1", "f1", "bogus"]),
        (["205006 "], [], ["205006 "]),                      # no trimming: ids compare exactly
    ]
    for fact_ids, chunk_ids, expected in cases:
        got = unresolved_citation_ids(ci.detail("s", fact_ids, chunk_ids), er, chunks)
        assert got == expected, (fact_ids, chunk_ids)


_INVENTED = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=12)


@pytest.mark.criterion("C1")
@given(
    real=st.lists(st.sampled_from(RESOLVING_FACT_IDS), max_size=6),
    invented=st.lists(_INVENTED.filter(lambda s: s not in RESOLVING_FACT_IDS),
                      min_size=1, max_size=4, unique=True),
    real_chunks=st.integers(0, 2),
    invented_chunks=st.lists(_INVENTED.map(lambda s: "chunk-" + s), max_size=3, unique=True),
    data=st.data(),
)
def test_exactly_the_invented_ids_are_named_in_citation_order(
        ci, real, invented, real_chunks, invented_chunks, data):
    # oracle: rule 1 — the expected list is the invented ids, read off the shuffled citation
    # list in order (fact ids first, then chunk ids); every real id resolves
    er, chunks = _evidence(ci)
    fact_ids = data.draw(st.permutations(real + invented))
    chunk_ids = data.draw(st.permutations([ci.CHUNK_AAPL_MDNA] * real_chunks + invented_chunks))
    expected = [f for f in fact_ids if f in invented] + [c for c in chunk_ids
                                                         if c in invented_chunks]
    got = unresolved_citation_ids(ci.detail("s", fact_ids, chunk_ids), er, chunks)
    assert got == expected


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_renaming_step_ids_consistently_keeps_the_answer(ci):
    # oracle: rule 1 depends on ids only through equality, so renaming every step id (and the
    # citations of it) by one injective map renames the result by the same map
    er, chunks = _evidence(ci)
    rename = {s["step_id"]: f"q2_{s['step_id']}" for s in er["steps"]}
    renamed = {"status": "OK",
               "steps": [{**s, "step_id": rename[s["step_id"]]} for s in er["steps"]]}
    cited = ["m_aapl", "nope", "s1", "b1", "zz9"]
    before = unresolved_citation_ids(ci.detail("s", cited, []), er, chunks)
    after = unresolved_citation_ids(
        ci.detail("s", [rename.get(c, c) for c in cited], []), renamed, chunks)
    assert before == ["nope", "zz9"]
    assert after == ["nope", "zz9"]
    # and an id that was valid before the rename no longer resolves after it
    assert unresolved_citation_ids(ci.detail("s", ["m_aapl"], []), renamed, chunks) == ["m_aapl"]
