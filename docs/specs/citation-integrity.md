# citation-integrity: every cited id resolves, every stated number matches its citation

Decided by the maintainer on 2026-11-07. Test strategy area E, nuance N11
([XbrlRag_Test_Strategy.md](../design/XbrlRag_Test_Strategy.md)): a frontier model cannot cite
an audited fact, so XbrlRag's provenance has to be airtight. These checks are deterministic: no
LLM, no database.

## Requirement

Today `XbrlFaithfulnessVerifier._resolve_fact_evidence` / `_resolve_chunk_evidence`
(`src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py`) skip a cited id that resolves to
nothing (`continue`). A statement that cites one real fact and one invented id is checked against
the real fact only, and the invented id reaches `/api/v1/answer`. Nothing checks the numbers in a
statement against the values it cites. A compound query's merged answer
(`XbrlRetrievalPipeline._merge_responses`) concatenates the sub-answers' `answer_details`, while
step ids such as the fast path's `f1` repeat in every sub-query, so a merged statement's step id
cannot be resolved.

1. **Unknown ids are rejected (R1).** A statement id *resolves* when, as a string, it equals:
   for a `fact_ids` entry, the `citation.fact_id` or the `step_id` of a non-`narrative` step in
   `execution_result["steps"]`, the `fact_id` of a row of a `screen` or `breakdown` step, or a
   value of a step's `component_fact_ids`; for a `chunk_ids` entry, the `chunk_id` of an entry of
   the response's `narrative_chunks` (chunks the query retrieved; a step's `linked_chunk_ids`
   are not retrieved chunks). A step resolves whatever its status. The verifier drops a statement
   that cites **any** id that does not resolve, whatever the LLM verifier would say about it.
   The evidence the verifier sends the LLM is resolved by the same rule, so a statement whose
   ids all resolve (a component fact id included) is never dropped as citing no evidence.
2. **Every id in the final answer resolves (R2).** After the verifier, every id of every
   surviving statement resolves against the execution result and narrative chunks of the
   (sub-)query that produced it. In a compound query the merged answer keeps every id exactly as
   the sub-answer had it and adds `"subquery_id": <SubQuery.id>` to each `answer_details` entry;
   `execution_result["subqueries"][subquery_id]` is the execution result the entry's ids resolve
   against. A single (not decomposed) query adds no `subquery_id`. A sub-answer statement that
   cites an id from another sub-query (a fact, a step id such as a screen's `s1`, or a chunk
   retrieved only there) does not resolve in its own sub-query and is dropped by rule 1.
3. **Numbers in the prose match their citations (R3).** Every number stated in a statement must
   match a value of its cited evidence, under the rules below, or the statement is dropped.
4. **Drops are recorded like other unsupported statements.** A statement dropped by rule 1 or 3:
   - is removed from `answer_details` and appended to `unanswered_parts` as
     `{"question_fragment": <statement>, "reason": "failed faithfulness check: <reason>"}`, where
     `<reason>` names every unresolved id (rule 1) or the text of every unmatched number
     (rule 3);
   - has an entry in `metadata["faithfulness"]["verdicts"]` with `"verdict": "UNSUPPORTED"`,
     its `index`, `statement`, `fact_ids`, `chunk_ids`, and either
     `"check": "unresolved_ids", "unresolved_ids": [<ids as str, in citation order>]` or
     `"check": "number_mismatch", "unmatched_numbers": [<text of each, in statement order>]`;
   - emits one trace event `log_event("decision", point="citation_integrity", check=<the same>,
     index=<i>, ids=[...])` (rule 1) or `numbers=[...]` (rule 3);
   - is decided without the LLM: it is not sent to the verifier LLM call (when every statement
     is dropped deterministically no verifier call is made). Rule 1 is checked first; a statement
     with an unresolved id is not number-checked.
   `metadata["fully_answered"]` becomes `False` and the summary caveat counts these drops, as for
   any `UNSUPPORTED` statement. A statement citing nothing keeps today's handling.
5. **The serving surfaces carry citations unchanged (R4).** `POST /api/v1/answer`
   (`AnswerResponse.answer_details`) and the MCP `answer` tool return every statement's
   `statement`, `fact_ids`, `chunk_ids` and `subquery_id` exactly as the verified (merged)
   response holds them.

### Stated numbers (rule 3)

A *stated number* is a run of digits in the statement, with an optional currency prefix (`$`,
`US$`, `NT$`, `USD`, `€`, `£`, `¥`), thousands commas, a decimal part, and either a scale
(`thousand(s)`, `million(s)`, `mn`, `mm`, `billion(s)`, `bn`, `trillion(s)`, `tn`, or a glued
`k`/`m`/`b`/`t` not followed by a letter) or a percent (`%`, `percent`). A glued `x` (a multiple,
`1.9x`) is allowed and ignored. Its **value** is the unsigned magnitude with the scale applied
(percent stays in percentage points). Its **step** is the unit of its last stated digit with the
scale applied (`$391.0 billion` → 1e8; `391,035 million` → 1e6; `14.1%` → 0.1; `$6.08` → 0.01).
Signs are not compared: direction is worded ("a net loss of", "fell"), and judging it stays with
the LLM verifier.

**Not stated numbers (exempt, never compared):**

| Exempt | Recognised as |
|---|---|
| Years | a bare 4-digit integer 1900–2100 (no currency, decimal, scale or percent) |
| Fiscal labels and ids | digits glued to a preceding letter: `FY2024`, `FY24`, `Q3`, `H1`, `s1` |
| Dates | an ISO date `2024-09-28`; a day after a month name or its abbreviation (`September 28, 2024`, `Sept. 28`) |
| Form names and compound words | digits followed by `-` and a letter: `10-K`, `20-F`, `5-year` |
| Ranks | an ordinal (`1st`, `2nd`, `3rd`, `4th`); digits after `#`, `No.`, `number`, `rank`, `ranked`, `ranks`, `top`, `bottom` |
| Counts of companies and periods | an integer followed by `companies`, `company`, `filers`, `filer`, `firms`, `issuers`, `peers`, `months`, `month`, `weeks`, `years`, `quarters`, `days` |
| Numbers in words | `three`, `second`: never extracted |

### Matching (rule 3)

The **cited evidence** of a statement is: each step and row its `fact_ids` resolve to; for a
cited `compute` step, the steps its plan `expr` references (by step id, transitively through
further `compute` steps; `response.plan["steps"][].id` / `.expr`); and the text of each cited
chunk. A stated number S (value s, step u_s) matches a candidate value v (compared as |v|) when

`|s − |v|| ≤ max(u_s, u_rule) / 2` (plus a relative float slack of 1e-9),

where u_rule is the approved 2026-11-07 precision for the candidate
([XbrlRag_Test_Strategy.md](../design/XbrlRag_Test_Strategy.md#number-comparison)):

| Candidate | Stated as | u_rule |
|---|---|---|
| a `lookup` value or a `screen`/`breakdown` row value whose unit names shares *and* money (`USD/shares`, `shares*USD`) | not percent | 0.01 (cents) |
| any other `lookup` value or row value | not percent | 10^(−`decimals`) when the evidence carries an integer `decimals`, else 0 |
| a `compute` value | not percent | 3 significant figures: 10^(⌊log10 |v|⌋ − 2) |
| any step or row value, and a row's `pct_of_total` | percent | 0.1 (percentage points), tried against both 100·|v| and |v| |
| a stated number in a cited chunk's text (extracted by the same rules) | same percent-ness | that number's own step |

A stated number coarser than the rule (`$391 billion` for 391,035,000,000) is a faithful
rounding and matches; precision is graded elsewhere (the eval grader), not here. Comparing
currencies is out of scope (the units-currency-integrity change).

## Acceptance criteria

- **C1**: `unresolved_citation_ids` returns exactly the cited ids that resolve to nothing, as
  strings, `fact_ids` before `chunk_ids`, each in citation order. Oracle: hand-built execution
  results from real corpus facts; rule 1.
- **C2**: The verifier drops every statement that cites an unresolved id, even when the LLM says
  `SUPPORTED`, records it per requirement 4 (unanswered part, `UNSUPPORTED` verdict with
  `check`/`unresolved_ids`, `decision` event), never sends it to the LLM, and keeps every other
  statement. Oracle: hand-listed survivors; rule 1 and requirement 4.
- **C3**: `stated_numbers` returns each stated number's value (exact for integers, rel 1e-12
  otherwise), step and percent flag, in statement order, and skips every exempt number. Oracle:
  hand-read statements; the stated-number rules above.
- **C4**: `unmatched_numbers` returns exactly the stated numbers that match no cited evidence
  under the matching rules. Oracle: hand arithmetic on real corpus values (shown in the tests).
- **C5**: The verifier drops a statement with an unmatched number, including a correct number
  cited to the wrong step and a number the synthesizer computed itself, records it per
  requirement 4 (`check: number_mismatch`, `unmatched_numbers`), never sends it to the LLM, and
  keeps a statement restating its cited value in another scale. Oracle: hand-listed survivors.
- **C6**: A compound query (a screen, then a dependent `<<TOP:sq1:2>>` compute over lookups) run
  end to end returns exactly the hand-listed surviving statements, each with its `subquery_id`
  and unchanged ids, every id resolving in its own sub-query; statements citing an invented id,
  another sub-query's fact or step id, or a correct number from the wrong step are dropped.
  Oracle: hand-built expected answer; rules 1–3.
- **C7**: `/api/v1/answer` and the MCP `answer` tool return the C6 answer's statements, ids and
  `subquery_id` exactly. Oracle: the hand-built expected answer of C6.

## Interface

New module `src/retriever/xbrl/citation_integrity.py`:

- `unresolved_citation_ids(detail: dict, execution_result: dict, narrative_chunks: list[dict]) -> list[str]`
- `stated_numbers(statement: str) -> list[dict]`: each `{"text": str, "value": float,
  "step": float, "percent": bool}`; `text` is the statement's own substring for the number.
- `unmatched_numbers(detail: dict, execution_result: dict, plan: dict, narrative_chunks: list[dict]) -> list[dict]`:
  the `stated_numbers` entries of `detail["statement"]` that match nothing.

Behaviour changes (no new signature): `XbrlFaithfulnessVerifier.verify` applies rules 1 and 3 per
requirement 4; `XbrlRetrievalPipeline._merge_responses` adds `subquery_id`.

## Notes

- **Data.** Values are real `xbrl_db` facts read on 2026-11-07 (`mv_financial_facts`,
  `mv_geographic_facts`, `mv_segment_matrix`; FY2024, `fiscal_period='FY'`, no dimensions):
  AAPL Revenue 391,035,000,000 (fact 13253, decimals −6), GrossProfit 180,683,000,000 (13265),
  NetIncome 93,736,000,000 (13289), EPS diluted 6.08 (13295, decimals 2); AMZN Revenue
  637,959,000,000 (6854), NetIncome 59,248,000,000 (6761); WMT Revenue 648,125,000,000 (205006),
  NetIncome 15,511,000,000 (205048); MSFT Revenue 245,122,000,000 (46957); AAPL China revenue
  66,952,000,000 (14256, 0.17121741 of total); AAPL iPhone revenue 201,183,000,000 (13550),
  Services 96,169,000,000 (13562). Chunk ids are synthetic UUID-shaped strings, as
  `_make_chunk_id` forms them; chunk texts are synthetic.
- **Fakes.** Only at I/O boundaries: a scripted provider adapter behind the real `LLMGateway`
  for each LLM stage (the pattern of the xbrlrag-run-halt spec), the entity resolver's SQL, the
  fact store (lookup, screen), and, for C7, the cached pipeline slot `src.api.pipelines` serves
  from, which holds a real pipeline built on those fakes.
- **Overlap.** No current check compares stated numbers with citations in the query pipeline.
  `src/eval/core/answer_parsing.extract_numbers` (arm-neutral-grading) skips years, ISO dates and
  form names the same way and may be built on; it returns no stated precision and exempts no
  ranks, counts or day-of-month, so it is not enough by itself. No graduated spec test asserts
  that an unresolved id is kept or that merged statements lack `subquery_id`; none changes.
- **Decisions taken by the spec author** (for review): chunk text counts as cited evidence for
  numbers (else every narrative statement quoting a number would be dropped); a cited compute
  step's inputs count as cited (a margin statement may restate its net income); signs are not
  compared; a step's `linked_chunk_ids` are not resolvable chunk ids; ids resolve whatever the
  step's status.
