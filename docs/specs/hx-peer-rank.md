# hx-peer-rank — grade H-X peer-relative cases with the stated-rank reader

[peer-rank-grading](peer-rank-grading.md) (implemented) reads a peer-relative rank
from what the answer states, else from its rows' values. It applies only to v2
`peer_relative` cases, which carry `case.subject`. The H-X `cross_filer` / `peer_relative`
cases ([hx-authoring](hx-authoring.md) requirement 14: built as a `count` case plus `ticker`)
have the sub-kind `"rank"` and are still graded as plain scalars
([v2-grading](v2-grading.md) requirement 9): the rank must appear among
`answer.numeric_values`.

That is wrong both ways. The number extractor drops ordinals
(`src/numerics/stated_numbers.py`), so "DEVB ranks 2nd" gives no 2 and a correct answer is
`INCORRECT`. And any stated integer that happens to equal the rank passes ("of the 4
retailers" when the rank is 4). This is the defect peer-rank-grading fixed for v2, on the
H-X side.

Decision (maintainer, 2026-11-10): reuse the peer-rank-grading reader for H-X, with the
inputs read from the H-X case's own fields. The H-X build and `v2_subkind` do not change.

## Requirement

1. **Routing.** A case whose `v2_subkind` is `"rank"` is graded by peer-rank-grading
   requirement 4 (the reader `_grade_peer_relative` implements), not by v2-grading
   requirement 9. `v2_subkind` still returns `"rank"` for it. Every other sub-kind of
   requirement 9 (`count`, `sum`, `median`, `cagr`, `ratio`) is graded as before.
2. **Inputs from the H-X case.**
   - **Subject.** The subject is `case.ticker` (upper-cased). The key's rank `r` is
     `int(case.expected_value)` when `expected_value` is a whole number of at least 1, else
     there is no `r`.
   - **Names.** `peer_names(case)`, plus each `answer.rows` entity, as in peer-rank-grading
     requirement 4.
   - **`n_ranked`** is `len(case.universe) − len(case.expected_exclusions)`, the H-X ranked
     filers.
3. **Grade, checks and downgrades** are peer-rank-grading requirement 4's: `checks =
   {"rank": right}` ∪ `disclosure_checks(case, answer.text)` (H-X disclosure data,
   v2-grading requirement 7). No subject or no `r` is `INCORRECT` with `checks["rank"]`
   false. When right, `matched_value = float(r)`, downgraded to `PARTIAL` when a disclosure
   check fails, and `PARTIAL` when `require_citation` and no `fact_ids`. An H-X rank grade's
   checks key is therefore `rank`, no longer `value`.
4. **Grounding.** `grounding_precision` and `grounding_recall` are those of
   `grade_grounding(case.expected_fact_ids, answer.fact_ids,
   require_exact=case.require_exact_fact, fact_id_classes=case.grounding_classes())`, as the
   scalar path recorded them. They never change the grade.
5. **Names for an H-X case** (extends peer-rank-grading requirement 3). The keys of
   `peer_names(case)` also include `case.ticker`, every ticker in `case.universe` and every
   `ticker` of `case.expected_exclusions`, upper-cased, each only when present. A v2 case has
   no `universe`, so its keys do not change. The values are as before (case names, then the
   committed lexicon, else `[]`).
6. **Grader version.** No stored grade changes (no H-X suite has been built), so
   `grader_version()` stays `"5"`.

## Acceptance criteria

A fictional H-X case in hx-authoring's built shape: universe DEVA, DEVB, DEVC, DEVD; DEVD
excluded (`not_tagged`); subject DEVB at rank 2 (DEVA 300m > DEVB 200m > DEVC 100m), so
`n_ranked` is 3. Answers are hand-built `SystemAnswer`s.

- **C1**: the rank is read as for v2 (requirements 1–3).
  - "DEVB ranks 2nd of the 3 ranked filers. DEVD was excluded." is `CORRECT`
    (`checks == {"rank": True, "universe": True, "exclusions": True}`, `matched_value` 2.0);
    with "3rd" it is `INCORRECT`.
  - No stated rank and no rows: the values rule decides (`values=[2.0]` is `CORRECT` with
    the `rank` check, `[3.0]` is `INCORRECT`), as the retired v2-grading C5 expects.
  - No stated rank: complete rows (3 valued entities, any order) decide by value; rows
    naming only DEVA and DEVB (2 < 3) imply no rank, so `INCORRECT`.
  - Correct but silent on DEVD is `PARTIAL`.
  - `expected_value` 2.5, or no `ticker`: `INCORRECT`, `checks["rank"]` false.

  Properties: permuting `answer.rows` never changes the grade or `checks`; renaming every
  ticker consistently in the case and the answer never changes the grade; for a stated
  ordinal rank `k` in 1–3, the grade is `CORRECT` exactly when `k` is 2. Oracle:
  requirements 1–3 and peer-rank-grading requirement 4, hand-derived grades.
- **C2**: grounding (requirement 4). Cited `[1, 99]` against classes `[[1], [2], [3]]`
  gives precision 1/2 and recall 1/3, and the grade is still `CORRECT`.

  Property: for any subset of `[1, 2, 3]` cited (non-empty) plus 0–2 unknown ids, precision
  is matched / cited and recall is matched / 3, counted in the test, and the grade is the
  same as with every fact cited. Oracle: requirement 4 and `grade_grounding`'s documented
  per-class rule.
- **C3**: names (requirement 5). `peer_names` of the case has the keys DEVA, DEVB, DEVC,
  DEVD, each with `[]` (fictional tickers, no lexicon entry).

  Property: for random universes, exclusions and subjects, the keys are exactly the union of
  `ticker`, `universe` and the exclusions' tickers. Oracle: requirement 5.

## Interface

No new function. Changed (implementer): `grade_v2`'s routing of the `"rank"` sub-kind and
the subject / rank / `n_ranked` inputs of the peer-rank reader (requirements 1–3), the
grounding of that grade (requirement 4), and `peer_names` (requirement 5), all in
`src/eval/core/graders/v2.py`.

## Notes

- **Oracles.** Every expected grade is derived by hand; the case and answers are fictional.
  No set B, H-X or held-out file is read.
- **Retired tests.** v2-grading C2 still expects `"rank"`; its C5 rank answers state no rank
  and carry no rows, so the values rule gives the same grades. peer-rank-grading's C4 property
  builds cases without `universe` or `ticker`, so its key union is unchanged. All must keep
  passing (`pytest test/spec`).
- **Template.** [configs/eval/hx_template.yaml](../../configs/eval/hx_template.yaml) flags the
  gap until this lands.
- **Known reader limit, out of scope.** The 11-10 re-grade found the reader gives "I ranked
  32 of the 33 companies" to the subject by the default owner rule (F4, a re-lock of
  peer-rank-grading's rule). H-X answers inherit whatever that fix decides.
