# citation-row-components — a row's component fact ids resolve to that row

Amends requirement R1 of [citation-integrity](citation-integrity.md) (implemented and retired;
that spec and its tests are unchanged). Found by a $0 trace replay on 2026-11-14 (dev traces in
`audit.query_event`, e.g. query `b9d0181ba6d04a9e`): since
[s4-screen-defined-members](s4-screen-defined-members.md)) a `Debt` / `DeferredRevenue` screen
row ranked by its S4 measure carries every part of the measure in `component_fact_ids`
(`{"part_1": 14500, "part_2": 14502, "part_3": 14506}` for Apple's FY2024 covered Debt row,
whose `fact_id` is the first part). The synthesizer is shown those ids in `FACT_RESULTS` and
cites them. A derived-metric screen row (`mv_derived_metrics`) has a synthetic `fact_id` and
its audited facts only in `component_fact_ids`.

**The defect (origin/main f7dbfe7).** `citation_integrity.evidence_index`
(`src/retriever/xbrl/citation_integrity.py`:75–83) indexes a *step's* `component_fact_ids` but,
for a `screen`/`breakdown` row, only `row["fact_id"]`. A statement citing
`["14500", "14502", "14506"]` therefore has `14502` and `14506` marked `unresolved_ids`, the
faithfulness verifier drops it deterministically, and a correct screen answer becomes a
refusal. In the replay, every statement of the S4 Debt screen in queries `b9d0181ba6d04a9e` and
`68b4f46e0e074de8` that cited a row's parts was dropped this way.

## Requirement

1. **R1, amended: a row's component ids resolve to the row.** In addition to the ids R1 of
   citation-integrity lists, a `fact_ids` entry *resolves* when, as a string, it equals a fact
   id listed by the `component_fact_ids` of a row of a `screen` or `breakdown` step. It resolves
   to that row's record, `{**row, "op": <the step's op>}`, exactly as the row's `fact_id` does.
   The ids a `component_fact_ids` value lists are: an object's values, or an array's items, in
   order; an item that is itself an array lists each of its items (one level); a `None` lists
   nothing. A row whose `component_fact_ids` is missing, `None` or empty adds nothing. Ids keep
   their form: they are compared as strings, statement ids are not rewritten, and no id is
   formed differently (no migration).
2. **Every consumer that resolves a cited fact id to a row uses the same rule.** A statement
   citing a row by any of its component ids is treated as citing that row:
   1. `citation_integrity.unresolved_citation_ids` (rule 1): the id is not unresolved.
   2. `citation_integrity.unmatched_numbers` (rule 3): the row is cited evidence (its `value`
      and `pct_of_total`, as for a row cited by its `fact_id`), and for a `screen` row the
      screen's counts (`screen_count_values`) and the row's `rank` are candidates, as
      verifier-derived-disclosures requirement 3 makes them for a row cited by its `fact_id`.
   3. `XbrlFaithfulnessVerifier.verify`: the statement's `fact_evidence` sent to the LLM holds
      the row's entry (the same entry a statement citing the row's `fact_id` gets: `step_id`
      `"<screen step id>:<row fact_id>"`, `op`, `value`, `unit`, `ticker`, `fiscal_year`, and
      for a screen row `rank` and `period_end`), once per row however many of its ids the
      statement cites, and the cited screen's `screen` summary entry
      (verifier-derived-disclosures requirement 4.2).
   4. The eval judge's claims (`src.eval.core.judge_wiring.answer_claims`): a claim citing a
      row's component id has that row's passage, `json.dumps(row, sort_keys=True,
      default=str)`, the passage a claim citing the row's `fact_id` gets (each passage once).
3. **Nothing else changes.** An id that resolves today still resolves to the records it does
   today; an id that is no step id, citation, step component, row fact id or row component id
   stays unresolved and its statement is dropped by rule 1. No prompt changes (non-goal).

### Consumers reviewed

Every place that maps a cited fact id to evidence, on origin/main f7dbfe7:

| Consumer | Reads row components today? | In scope |
|---|---|---|
| `citation_integrity.evidence_index` → `unresolved_citation_ids` (rule 1) and `_cited_candidates` (rule 3) | no | **yes** (R2.1, R2.2): drops the statement |
| `citation_integrity._screen_candidates` (screen counts, cited rows' ranks) | no (`row["fact_id"]` only) | **yes** (R2.2): a stated count or rank becomes a number mismatch |
| `XbrlFaithfulnessVerifier._index_fact_steps` / `_resolve_fact_evidence` / `_cited_screens` (evidence sent to the LLM) | no | **yes** (R2.3): the LLM sees no evidence for the statement and may mark it unsupported |
| `src/eval/core/judge_wiring._fact_passage` (judge claim passages) | no | **yes** (R2.4): the judge grades a cited claim as uncited |
| `src/eval/core/graders/numeric.all_fact_ids`, `has_fact_citation` | yes | no: already right |
| `src/eval/systems/xbrlrag_full._row_fact_ids` / `answer_rows` (stated rows, grounding) | yes | no: already right |
| `XbrlRetrievalPipeline._log_missing_year_length_notes` (uses `evidence_index`) | n/a | no: looks only at `compute`/`cagr` steps; a row record is never one |
| `XbrlRetrievalPipeline._merge_responses`, `/api/v1/answer`, MCP `answer` | n/a | no: carry ids unchanged (citation-integrity R4) |
| `ui/src/components/sourcesRegistry.ts` (Sources panel) | display only | no: UI, affects neither the answer nor its grade |

## Acceptance criteria

- **C1**: `unresolved_citation_ids` returns exactly the hand-listed unresolved ids when
  statements cite screen and breakdown rows by their component ids (object and array forms,
  one nested array level, a derived row with a synthetic `fact_id`), and `evidence_index` maps
  each component id to a record equal to `{**row, "op": op}`. Oracle: R1 as amended, applied
  by hand to hand-built execution results (exact, no tolerance).
- **C2**: `unmatched_numbers` returns `[]` for a statement citing a screen row only by a
  component id and stating the row's value, the screen's ranked count and the row's rank, and
  returns exactly the hand-listed numbers a wrong value or an uncited row's value leaves
  unmatched. Oracle: R2.2 and citation-integrity's matching rule, hand arithmetic shown in the
  tests.
- **C3**: The verifier keeps a statement citing a row's component ids (its LLM answering
  SUPPORTED), sends it to the LLM with exactly one entry for that row carrying the hand-stated
  fields and the screen's `screen` entry, and still drops a statement that also cites an
  invented id. Oracle: R2.3 and citation-integrity requirement 4.
- **C4**: `answer_claims` gives a claim citing a row's component id that row's passage
  (`json.dumps(row, sort_keys=True, default=str)`, built in the test), once, and no passage for
  an id that resolves to nothing. Oracle: R2.4.

Each criterion has a property test: random rows with random component ids, where every
component id resolves to its row (C1, C2, C3) or its row's passage (C4), adding component ids
to rows never makes a resolved citation unresolved, and an id outside every step and row stays
unresolved.

## Interface

No new function. Behaviour changes (existing signatures unchanged):

- `src.retriever.xbrl.citation_integrity.evidence_index(execution_result: dict | None) -> dict[str, list[dict]]`
- `src.retriever.xbrl.citation_integrity.unresolved_citation_ids(detail: dict, execution_result: dict, narrative_chunks: list[dict]) -> list[str]`
- `src.retriever.xbrl.citation_integrity.unmatched_numbers(detail: dict, execution_result: dict, plan: dict, narrative_chunks: list[dict]) -> list[dict]`
- `src.retriever.xbrl.llm_xbrl_faithfulness_verifier.XbrlFaithfulnessVerifier.verify`
- `src.eval.core.judge_wiring.answer_claims(answer: SystemAnswer) -> list[Claim]`

## Notes

- **Data.** Apple, Oracle and Broadcom FY2024 covered `Debt` screen rows as the S4 measure
  builds them (`xbrl_db`, read-only, by the trace replay of 2026-11-14): AAPL 106,629,000,000
  (parts 14500, 14502, 14506; period end 2024-09-28), ORCL 86,869,000,000 (71847, 71859;
  2024-05-31), AVGO 67,566,000,000 (26581, 26587; 2024-11-03). The screen's disclosure (members,
  ranked list, exclusions), the breakdown row's extra component id and the derived row are
  synthetic.
- **Fakes.** Only the verifier's LLM: a scripted provider adapter behind the real `LLMGateway`
  (the pattern of the citation-integrity and verifier-derived-disclosures specs).
- **Out of scope.** A statement stating a *part's* value (the row carries the parts' ids, not
  their values, and has no `definition.status == "defined"`) is still a number mismatch: the
  synthesizer is to state the measure, and showing part values is a separate change. The judge's
  passage for a *step's* `component_fact_ids` (a derived lookup's or compute's parts) is also
  missing today (`_fact_passage` reads `citation.fact_id` and `step_id` only); it is a separate
  gap of the same kind, left for a follow-up decision.
- **Decision taken by the spec author** (for review): where one id is both a component of a row
  and some record's `fact_id`, `citation.fact_id` or a step's component (as AAPL's `part_1` is
  the row's own `fact_id`), it resolves to every such record in `evidence_index`; in the
  verifier's single-valued index the record it maps to today keeps precedence. A component id
  shared by two rows gives the judge one passage (the first such row's, as `_fact_passage`
  returns one passage per id today). No test pins either precedence beyond R2.3's "one entry
  per row"; the C4 property test draws component ids that no two rows share.
