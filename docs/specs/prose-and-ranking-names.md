# prose-and-ranking-names: text readers read the answer's prose; a generated compound's ranking is a set of names

**Route:** locked spec (eval grading: a wrong grade goes unnoticed, and an independent answer
exists). **Decided:** 2026-11-20 by the maintainer, after a blind human review of 30 development
answers; disclosed by Amendment 5 of `docs/eval/heldout_v2/preregistration.md`. Follows
[compound-value-correct](compound-value-correct.md) (grader 13, Amendment 4).

## Requirement

`value_correct` is the arm-neutral metric ([arm-neutral-grading](arm-neutral-grading.md)). After
grader 13, the maintainer marked blind (right / wrong / unclear) the 30 answers of the two
`frontier_sec_tools` arms (Opus `eval-6d3120902a79`, deepseek-v4-pro `eval-09d1506ac876`) to 15
development compound cases (`data/eval/slices/cvc_agents/dev_compound_15.json`, 8
`compound.leader_growth` and 7 `compound.rank_by_ratio`). Marked right: 20; grader 13 credits 7.
It credits no answer marked wrong. All 13 answers it fails have the right final value; two causes
explain 11 of them:

- **The text readers read a JSON envelope.** A baseline arm (`frontier_sec_tools`,
  `long_context_oracle`, `closed_book`) replies with a JSON object; `parse_structured_answer`
  keeps the whole reply as `SystemAnswer.text`. Every deterministic text reader of the v2 grader
  (the pick reader `picked_filer`, the disclosure and phrase checks, stated ranks, stated
  aggregates) therefore reads the object's `rows`, `statements` and other fields as prose.
  XbrlRag's text is prose. The judge already reads the object's `"text"`
  ([judge-baseline-claim-text](judge-baseline-claim-text.md), `answer_prose`). Three answers fail
  because the pick reader finds the wrong winner in the envelope (a958 and c556 of
  deepseek-v4-pro; and 87ce, which also fails its ranking).
- **The ranking part of a generated compound compares order and figures the question does not
  ask for.** Both generated shapes ask about "the top N": which of them has the highest ratio
  (`rank_by_ratio`), or the growth of the one ranked first (`leader_growth`, whose dependent value
  is the leader's). Eight answers list the right N companies in `rows` but carry the second
  step's values there (each company's ratio or growth), ordered by them: the ranking part fails
  on "top-1 mismatch" or "value agreement". The maintainer decided (2026-11-20) that the ranking
  part matches on the names only.

When this change is done:

1. **Prose.** The v2 grader (`grade_v2`, and `grade_case` for a v2 case) grades an answer whose
   `text` holds a JSON answer object exactly as it grades the same answer with `text` replaced by
   `answer_prose(text)`: the same `grade`, `passed`, `value_correct`, `checks`, `confident_wrong`
   and `reason`. An answer whose text holds no such object (XbrlRag's prose) is graded as before.
   `numeric_values`, `rows`, `fact_ids`, `refused` and `hedged` are read as before.
2. **Names.** In a compound case of shape `compound.rank_by_ratio` or `compound.leader_growth`,
   the ranking part (`"part": "ranking"`, kind `screen`) is right when the set of the first `N`
   entities of the answer's `rows` equals the set of the first `N` entities of the expected rows
   (`N` the part's `top_n`, else the number of expected rows), for the key's expected rows or for
   any reading the grader already forms from them (don't-care members and ties,
   [grading-dont-care-and-ties](grading-dont-care-and-ties.md)). The order of the rows and their
   values are not compared. Fewer than `N` row entities is not a match.
3. A ranking part that is right by requirement 2 is graded as a standalone screen whose ranking
   matched: `CORRECT` when the answer carries the citations and grounding the part requires, else
   `PARTIAL` and value-correct; its disclosure checks (universe, exclusions, period ends) apply
   as before. A ranking part whose names do not match is graded as before.
4. Nothing else changes: an H-X compound's ranking part, a standalone screen and every other
   kind are graded as before (requirement 1 aside); a compound's roll-up is grader 13's.
5. The grader version rises to at least 14.

**Measured on development data only, at no cost (2026-11-20),** with a prototype of requirements
1-3:

- Requirement 1 alone over 1,061 stored dev answers (both tools agents on the 100-case dev sample
  and the 15 cases, the oracle on Sonnet and v4-pro, closed-book on the full dev suite, two
  XbrlRag runs): `value_correct` changes for 2 answers (a958 and c556 of v4-pro, both to right),
  and one other grade moves (a median case of the Opus tools agent, REFUSED to HEDGED_MISS, not
  value-correct either way). XbrlRag's 236 answers carry no JSON object and do not change.
- Requirements 1-3 over the 30 reviewed answers: 18 of the 20 marked right are value-correct,
  none of the 10 marked wrong. The two still failed are single cases left as known misses:
  684520 (v4-pro) states its top 3 only in prose with no `rows`, and 7a3c (v4-pro) names only the
  leader. A reader of rankings stated in prose is not built (one measured case each).
- XbrlRag's two dev compound runs (`eval-c78dd0934732`, `eval-223d517429f8`, 136 cases each):
  118 and 114 value-correct and passed, before and after; no case moves.

## Acceptance criteria

- **C1**: A `compound.rank_by_ratio` answer whose JSON object's `"text"` names DEVB as having the
  highest operating margin, at 30.0%, with the right ranking rows, is value-correct, though the
  object's other fields (rows of the whole universe, statements naming DEVC as highest) precede
  it; its grade equals that of the same answer with `text` set to the `"text"` string. For any
  fixture case and any answer, wrapping the answer's prose in a JSON object with arbitrary other
  fields (statements and key orders drawn at random) leaves `grade`, `passed`, `value_correct`,
  `checks`, `confident_wrong` and `reason` unchanged. Oracle: requirement 1 (the prose grade is
  the reference; the right answer is the fixture's key, DEVB at 30.0%).
- **C2**: For both generated shapes, an answer whose `rows` hold the key's top three (DEVA, DEVB,
  DEVC) in any order with any finite values (the second step's figures, say) has a value-correct
  ranking part and, with the right dependent, a value-correct compound: `CORRECT` when cited,
  `PARTIAL` and value-correct when uncited. Oracle: requirement 2-3 with the fixtures' keys (top 3
  by FY2024 revenue DEVA 500m, DEVB 400m, DEVC 300m of five members).
- **C3**: For both generated shapes, rows whose first three entities are not the set {DEVA, DEVB,
  DEVC} (one replaced by DEVD or by the excluded DEVE, only two of them, or the three listed after
  another company) leave the ranking part and the compound not value-correct, while the same
  answer with the right set is value-correct. Oracle: requirement 2.
- **C4**: An H-X compound (ranking + trend, compound-value-correct's fixture) with a reversed
  ranking is still `INCORRECT`, not value-correct, and confident-wrong when cited; a generated
  compound whose rows are the reversed right set is value-correct; a generated compound with the
  right set that does not state its universe is not `CORRECT` and its `ranking.universe` check is
  false. Oracle: requirements 3 and 4.
- **C5**: `int(grader_version()) >= 14`. Oracle: requirement 5.

## Interface

No new public interface. The criteria read the existing `src.eval.core.graders.v2.grade_v2`,
`src.eval.core.graders.dispatch.grade_case`, `src.eval.core.answer_parsing.answer_prose`,
`CaseGrade` and `src.eval.core.graders.honesty.grader_version`. Where the prose is taken (once at
the entry of `grade_v2`, or in each reader) is the implementer's choice.

## Notes

- Fixtures are synthetic (fictional DEVA..DEVE filers): the generated shapes from
  compound-value-correct's `generated_cases.json` (copied), and its H-X ranking + trend compound.
- **Updated retired test.** compound-shape C7's
  `test_correct_exactly_when_both_parts_are` used the reversed top three as its wrong ranking;
  under requirement 2 a reversed list of the right three is right. Its wrong ranking becomes
  DEVB, DEVC, DEVD (a wrong set; it fails under both graders).
- **Not in scope.** The `rows` of the tools agents' answers often carry the second step's values;
  the agent prompt is not changed (the look's answers are fixed). A suite-key defect seen in the
  same review (case 9bbd: the key used INTC's `NonoperatingIncomeExpense`, 629m, though its
  preferred `OtherNonoperatingIncomeExpense`, 172m, exists) is a known limitation recorded by
  Amendment 5, not graded around here.
- **After the merge of the implementation:** Amendment 5 is merged before any re-grade; the
  maintainer re-grades the stored G1 and G2 answers at no cost (`regrade_g14.ps1`) and runs
  `g1_results.py --ledger g14`. The measurements above are repeated with the implementation in
  the implementation PR.
