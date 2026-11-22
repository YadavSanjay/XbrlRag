# v2-grounding: grounding precision for v2 cases, from the answer key's fact identity

Grounding precision ("the right fact cited, not merely a fact") is a headline metric and a line
of the success bar: "grounding precision ≥ 95%", XbrlRag only, "not applicable" for the agents
([preregistration](../eval/heldout_v2/preregistration.md) §3 and R1;
[architecture](../design/XbrlRag_Eval_v2_Architecture.md) §8). Today it is NULL on every stored
`xbrlrag_full` run graded with a v2 suite (`eval-9a99afc97d4b`, `eval-42942572934b`,
`eval-95c14a787892` and every other v2 run), while the v1 `sql_derived` runs carry it
(`eval-0aab4438d8aa`: 472 of 482 cases).

**Root cause.** Every grader computes grounding with `grade_grounding(case.expected_fact_ids,
answer.fact_ids, fact_id_classes=case.grounding_classes())`
(`src/eval/core/graders/dispatch.py`, `v2.py` `_with_rows_evidence`). With no expected ids,
`grade_grounding` returns `precision=None, recall=None` by design ("an unlabelled case must not
be scored as a failure"). The v1 suite is built from the fact store, so each case carries
`expected_fact_ids`. The v2 keys are built from SEC companyfacts, independently of the store
(S1–S6): no v2 case has `expected_fact_ids` (628 of 628 dev cases), so grounding is never
asserted. The answer side is fine: 581 of the 628 cases of `eval-95c14a787892` store cited
`fact_ids` in `produced`, and every one of the 624 distinct ids resolves in `mv_financial_facts`.

What a v2 case does carry is the key's **fact identity**: `key.inputs[i]` with `cik`,
`concept`, `unit`, `period_start`, `period_end`, `value`, `decimals`, `accession` and
`selection`, plus disclosed readings (`key.alternates`, `alt_readings`). Grounding for a v2 case
is therefore decided by resolving each cited `fact_id` through the fact store and comparing its
identity with the key's. The store keeps its own conventions, so the comparison normalises them
first: concepts as `<namespace URI>:<local>` (`http://fasb.org/us-gaap/2026:Revenues`), a
per-share unit as `shares*USD`, and Arelle's next-midnight period end (the existing
`FactStore` already moves the end back one day).

Route: locked spec. Grounding precision decides a bar line, and a wrong rule still prints one
plausible percentage. The right answer is stated independently: hand-built keys, hand-built
fact records and hand-counted precision.

## Requirement

### Scope

1. **Applicable cases.** A case is *v2-grounded* when `case.key` is a dict whose `inputs` is a
   non-empty list **and** `case.expected_fact_ids` is empty. Every other case (the v1 suites,
   golden, narrative, honesty and text cases, a key without inputs) is graded exactly as today:
   nothing in this change touches its grounding fields.
2. **What counts as cited.** The cited facts of an answer are the distinct ids in its
   `fact_ids` (for XbrlRag: every `fact_id` its OK execution steps rest on, `all_fact_ids`;
   stored in `produced.fact_ids`), first occurrence first. A repeated id counts once.

### Slots: what the key says the answer rests on

3. `v2_grounding_slots(case)` lists the case's slots: `[]` for a case that is not v2-grounded;
   otherwise one **key slot** per entry of `key.inputs`, in order, then one **reading slot** per
   disclosed reading, in this order: `key.alternates`, `case.alt_readings`, then each
   `expected_rows[j].alt_readings` in row order. A reading entry without `value`, `concept` or
   `period_end` is skipped.
   - A key slot `k` has `role="key"`, `slot=k`; `cik` = `int(input.cik)`, else `int(case.cik)`;
     `concept`, `unit`, `period_end`, `value` (float) and `accession` as written;
     `period_start` as written, `None` for an instant (`null` or empty); `decimals` the input's
     integer `decimals`, else `None` (`null`, `"INF"`); `defined` = the concept starts with
     `s4:` (a defined key: `s4:debt_total`, `s4:deferred_revenue_total`, key-definitions);
     `bind_accession` = the case is a point-in-time case (it has the tag `shape:point_in_time`,
     or `v2_subkind(case) == "point_in_time"`).
   - A reading slot has `role="reading"`, `unit=None`, `period_start=None`,
     `bind_accession=False`, `defined=False`; `cik` = the reading's own `cik`, else
     `case.cik`, else the first key slot's `cik`; `concept`, `period_end`, `value`, `decimals`
     and `accession` as written. It **serves** (`slot`) key slot 0 when the case has exactly one
     input; otherwise the first key slot with the same `cik` and `period_end`; otherwise none
     (`slot=None`).

### Matching a cited fact

4. **Normalising a fact record.** A `FactRecord` (`src/eval/sec_check/base.py`) is compared in
   companyfacts form:
   - concept: when the part before the last `:` maps through `taxonomy_key` (us-gaap, ifrs-full,
     srt, dei), `"<taxonomy>:<local>"`; any other concept unchanged (an extension never equals a
     key concept);
   - unit: `companyfacts_unit` (`shares*USD` and `USD*shares` become `USD/shares`; others
     unchanged);
   - `cik` as an int; `period_start` (`None` for an instant), `period_end` (SEC date) and
     `accession_no` as in the record.
5. **Value agreement.** `agree(v, k, d)` holds when
   `|v − k| ≤ max(t, 1e-9 · max(|v|, |k|, 1))`, where `t = 0.5 · 10^(−d)` for an integer
   `d` and `0` otherwise. (At `decimals = −6` two values agree when they round to the same
   million; with no decimals they must be equal to 1e-9 relative.)
6. **Ordinary slot.** A fact matches a key slot that is not `defined` when all hold: the fact
   is not `dimensional`; `cik`, normalised `concept`, normalised `unit`, `period_start` and
   `period_end` equal the slot's; `agree(fact.value, slot.value, slot.decimals)`; and, when
   `bind_accession`, `accession_no` equals the slot's `accession`. A fact matches a reading slot
   on the same rule without the `unit`, `period_start` and accession comparisons.
7. **The accession rule (S1).** Outside a point-in-time case the accession is **not** compared.
   S1 makes the key the value in the latest filing that reports the period; the product answers
   from the FY N filing's column as the latest later-labelled canonical filing reports it, and
   a later comparative of the same period carries the same fact under another accession. When
   the later filing restated the period, the earlier filing's value differs and fails rule 5,
   so the restated value is still the only one that counts. A point-in-time question names the
   filing ("in the FY2024 annual report"), so there the citation must come from the key's
   accession even when another filing reports the same value.
8. **Defined slot.** For each `defined` key slot `s`, let `C` be the cited facts, in cited
   order, that resolve, are not dimensional, have `cik`, normalised `unit`, `period_start` and
   `period_end` equal to `s`'s, and match no ordinary key slot or reading slot (rule 6). Then:
   the facts of `C` whose own value agrees with `s.value` (rule 5) are right; if there are none,
   and `C` is not empty, and the sum of the values of `C` agrees with `s.value`, every fact of
   `C` is right; otherwise no fact is right through `s`. The case does not record a defined
   key's components, so a cited presented line or tagged total is right when it is the total,
   or when the cited lines of that filer and date add up to it.

### Precision and recall per case

9. `grade_v2_grounding(case, fact_ids, facts)` returns `None` for a case that is not
   v2-grounded. Otherwise a `V2Grounding`:
   - `right`: the cited ids that resolve (are keys of `facts`) and match some slot (rules 6
     and 8), in cited order; `wrong`: every other cited id, in cited order, including ids that do
     not resolve; `unresolved`: the cited ids missing from `facts`, in cited order;
   - `evidenced`: the sorted indices `k` of the key slots evidenced: an ordinary key slot `k`
     is evidenced when a right fact matches it or matches a reading slot serving `k`; a defined
     slot when rule 8 made at least one fact right through it;
   - `precision = len(right) / (number of cited ids)`, or `None` when nothing is cited;
   - `recall = len(evidenced) / (number of key slots)`; `0.0` when nothing is cited.

   Every v2 shape follows from the key's inputs: a lookup has one slot; a compute (ratio,
   difference, growth, CAGR) one slot per input, so every input must be cited for recall 1.0;
   a trend one per year; a point-in-time case one per reading the key holds; a screen, a sum,
   a median and a peer-relative case one per filer the key ranks (each ranked row's input, a
   ratio screen's numerator and denominator per filer); a compound the top-level `key.inputs`
   (the ranking's inputs plus the dependent's).
10. **Refusals and arms that cite nothing.** An answer with no cited id (a refusal, a decline,
    a baseline or agent arm) has `precision=None` and `recall=0.0`: precision measures the
    citations made and has none to measure. It never enters the precision mean, so a system
    that cites nothing has a run precision of `None`, which the reports show as "not applicable"
    (preregistration R1).
11. Grounding never changes a case's grade, `passed`, `value_correct`, `silent_wrong` or
    `confident_wrong`. It sets `grounding_precision` and `grounding_recall` only.

### Attaching grounding to graded results, live and on a re-grade

12. `attach_v2_grounding(results, store)` takes graded `CaseResult`s and a `FactStore` (the I/O
    boundary: `PostgresFactStore` in production, a fake in tests). For each result whose case is
    v2-grounded, it sets `result.grade.grounding_precision` and `grounding_recall` to those of
    `grade_v2_grounding(result.case, result.grade.produced["fact_ids"], facts)`, where `facts`
    is `store.facts(...)` over the cited ids of those results (a missing `fact_ids` reads as
    `[]`); every other result is left unchanged. It returns the number of v2-grounded results.
    It reads only `produced`, which a stored run keeps (`result_payload`, `checkpoint_payload`),
    so a `CaseResult` rebuilt from a stored payload gets the same grounding as the live one:
    the re-grade costs no model call.
13. **Run aggregation.** The run's grounding precision and recall stay the existing
    `Metrics.compute` means: over the cases whose value is not `None`, each case weighted once
    (a macro mean, as the v1 runs are reported). The bar line "grounding precision ≥ 95%" reads
    this run-level precision over XbrlRag's H-F cases.
14. **Wiring** (not spec-tested; the implementer's unit tests cover it). `run_suite.py` calls
    `attach_v2_grounding` after grading when the system is `xbrlrag_full` (or any system whose
    answers carry `fact_ids`) and a store is reachable, before the run is persisted;
    `regrade_run.py` calls it on the re-graded results before storing them. A run graded
    without a store keeps `None` and says so in its log; it never writes `0.0`.

## Acceptance criteria

All values are exact (no float tolerance beyond the stated `agree`) unless a criterion says
otherwise. Fact records are hand-built in store form (URI concepts, `shares*USD`).

- **C1**: Scope. `v2_grounding_slots` is `[]` and `grade_v2_grounding` is `None` for a case
  without `key`, with `key.inputs == []`, or with `expected_fact_ids`; `attach_v2_grounding`
  leaves such a result's `grounding_precision`/`grounding_recall` exactly as they were (e.g.
  `0.75`/`1.0` set by the v1 grader) and does not count it. Oracle: requirement 1.
- **C2**: Slots. For a hand-built lookup with one input and one `key.alternates` cover-page
  reading, the slots are exactly the two slot dicts of requirement 3 (fields written out
  in the test); a point-in-time case's key slot has `bind_accession=True`; a two-input ratio's
  slots are its inputs in order, `slot` 0 and 1; a trend row's `alt_readings` entry serves the
  key slot with its `period_end`; a reading in a multi-input case matching no input has
  `slot=None`; a `s4:debt_total` input is `defined`. Property: permuting the inputs permutes
  the key slots the same way. Oracle: requirement 3, by hand.
- **C3**: Ordinary match. A lookup key (us-gaap:OperatingIncomeLoss, USD, 2023-01-01 to
  2023-12-30, 93,000,000, decimals −6) and the fact citing it in store form
  (`http://fasb.org/us-gaap/2025:OperatingIncomeLoss`) give precision 1.0, recall 1.0; a
  per-share key (`USD/shares`) matches a `shares*USD` fact. Changing any one of `cik`,
  `concept`, `unit`, `period_start`, `period_end` or `dimensional` makes it wrong (precision
  0.0, recall 0.0). At decimals −6, 93,400,000 agrees and 93,600,000 does not; with no
  decimals 93,000,001 does not agree. Property: for any single-field perturbation, precision
  is 0.0. Oracle: requirements 4–6, by hand.
- **C4**: The accession rule. Outside a point-in-time case, a fact with the key's identity and
  an agreeing value is right under any accession; a fact from the key's period in an earlier
  filing with a restated (different) value is wrong. In a point-in-time case the same-value fact
  from another filing is wrong and the key's own accession is right. Property: outside a
  point-in-time case, re-labelling every cited fact's accession never changes precision or
  recall. Oracle: requirement 7 and S1, by hand.
- **C5**: Defined keys. A `s4:debt_total` key of 14,048,000,000: a cited LongTermDebt fact of
  that value alone is right (1.0); cited LongTermDebtCurrent 1,000,000,000 and
  LongTermDebtNoncurrent 13,048,000,000 are both right (1.0, recall 1.0); cited components of
  42,150,000,000 against a total of 44,111,000,000 are wrong (0.0); in a debt-to-equity case the
  cited equity fact matches its own ordinary slot and is not counted in the debt sum. Property:
  any split of the total into 1–4 positive integer components, all cited, gives 1.0; dropping
  one of at least two components gives 0.0. Oracle: requirement 8 and S4's debt definition, by
  hand.
- **C6**: Readings. A cover-page shares fact (`dei:EntityCommonStockSharesOutstanding`, the
  reading's date and value) is right and evidences the lookup's key slot (recall 1.0); a deferred
  revenue `current_presented` reading on a trend row is right and evidences that year's slot.
  Property: citing the key fact, the reading fact or both always gives recall 1.0 and precision
  1.0. Oracle: requirements 3 and 6, by hand.
- **C7**: Arithmetic over shapes. Hand counts: a ratio citing numerator and denominator 1.0 /
  1.0; numerator and an unrelated fact 0.5 / 0.5; a 7-filer screen citing its three top rows
  1.0 / 3⁄7; the same plus an unresolved id 0.75 / 3⁄7 with that id in `wrong` and
  `unresolved`; a compound citing the three ranked rows and both dependent inputs of a 9-input
  key 1.0 / 5⁄9; a repeated id counts once. Metamorphic: permuting the cited ids never changes
  precision or recall; adding an id that resolves to an unrelated fact lowers precision whenever
  it was above 0 and never changes recall; adding a right id for an unevidenced slot never
  lowers precision or recall. Oracle: requirement 9, by hand.
- **C8**: Nothing cited. `fact_ids == []` gives precision `None`, recall `0.0`, empty
  `right`/`wrong`; after `attach_v2_grounding` on a run where no answer cites anything (an agent
  arm), `Metrics.compute(results).grounding_precision` is `None`. Oracle: requirement 10.
- **C9**: Attaching. On three hand-built results (two v2-grounded, one v1) and a fake store,
  `attach_v2_grounding` returns 2, sets each v2 result's fields to its hand-counted precision
  and recall, never changes `grade`, `passed` or `value_correct`, and
  `Metrics.compute(results).grounding_precision` is the mean of the non-`None` per-case values.
  A result rebuilt with `CaseResult.from_dict(checkpoint_payload(result), case)` (the stored
  form) gets the same values. Property: attaching twice gives the same fields as attaching once.
  Oracle: requirements 12–13, by hand.

## Interface

New module `src/eval/core/graders/grounding_v2.py`:

- `v2_grounding_slots(case: EvalCase) -> list[dict[str, Any]]`: each slot a dict with exactly
  the keys `role` (str), `slot` (int | None), `cik` (int), `concept` (str), `unit`
  (str | None), `period_start` (str | None), `period_end` (str), `value` (float), `decimals`
  (int | None), `accession` (str | None), `bind_accession` (bool), `defined` (bool).
- `grade_v2_grounding(case: EvalCase, fact_ids: Sequence[int], facts: Mapping[int, FactRecord]) -> dict[str, Any] | None`:
  a dict with exactly the keys `precision` (float | None), `recall` (float | None), `right`,
  `wrong`, `unresolved`, `evidenced` (each a tuple of ints). The requirement text calls this
  dict a `V2Grounding`.
- `attach_v2_grounding(results: Sequence[CaseResult], store: FactStore) -> int`

(Plain dicts, not NamedTuples: the spec branch may add only stub functions, and a typed result
class would have to arrive with the implementation. The implementer may add a TypedDict for these
shapes as long as the returned values stay plain dicts that compare equal as above.)

`FactRecord` and `FactStore` are the existing I/O seam in `src/eval/sec_check/base.py`
(`PostgresFactStore` reads `mv_financial_facts`, then `mv_financial_facts_multidim`, read-only,
with timeouts). No new interface is added for the store.

## Notes

- **Oracles.** Every expected value is a hand count over hand-built keys and records. The
  identities in the tests are modelled on the real dev cases they were read from (INTC FY2023
  operating income; TXN 2025 debt; QCOM cover-page shares; ORCL deferred revenue
  `current_presented`) but the tests carry only the synthetic records, no `data/` file.
- **Measured on a dev run (prototype of this definition, read-only SQL, no model call).** On
  `eval-95c14a787892` (dev, 628 cases) against the current dev freeze, 581 cases cite
  something: run precision 0.960, recall 0.859; precision 0.988 on its CORRECT cases and 0.82 on
  its INCORRECT ones (1,594 case citations in all). Before normalising units, every per-share citation read as wrong
  (`shares*USD`); before normalising concepts, every citation did. Value-equal citations of a
  different concept (for example `NetIncomeLoss` against a `ProfitLoss` key with no
  non-controlling interest, `RevenueFromContract…` against `Revenues`) were 9 of the 1,594 citations;
  they count as wrong under this rule (decision 2). No validation or held-out run was read.
- **Rollout.** After the implementation merges: a $0 re-grade (`regrade_run.py`, which then
  calls `attach_v2_grounding`) of every stored `xbrlrag_full` v2 run on the current freeze,
  dev first, each stored as a new run with its `config["regrade"]` provenance. Fact ids are
  store row ids: re-grade only runs whose `corpus_fingerprint` equals the store's, and read the
  `unresolved` count (logged by the CLI) as a sign the store changed since the run; the
  fingerprint alone does not reliably change on a load.
- **The H-X suite** is hand-authored and its cases may not carry `key.inputs`; such cases stay
  `None` (requirement 1). The bar is read on H-F, which the v2 builder writes with keys.
- **Docs to update with the implementation:** the metric table of
  `docs/design/XbrlRag_Evaluation.md` (v2 definition beside the v1 class definition) and
  `docs/design/XbrlRag_Eval_v2_Architecture.md` §8.

## Decisions for the maintainer

1. **Accession under S1** (requirement 7). Chosen: identity plus value agreement, with the
   accession bound only for point-in-time cases. Alternative: always require the key's
   accession, which would mark right numbers from a later comparative as wrong (61 of the 1,594
   dev citations, about 4%). Recommendation: as chosen.
2. **A different concept with the same value** (requirement 6). Chosen: wrong; the right fact
   is the key's element (or a disclosed reading's). Alternative: accept a member of the same
   concept family when the value agrees, which needs the family map (`concept_mapping`) as a
   second I/O seam and inherits families that held different measures
   (family-measure-equivalence). Recommendation: as chosen; revisit if the 9-citation effect grows.
3. **Defined keys without components** (requirement 8). Chosen: the cited lines of the filer
   and date that add up to the total, or a single fact equal to it. Alternative: have the suite
   builder record each defined key's components (concept and value) and match them by element;
   that changes the suite hash and needs a rebuild. Recommendation: as chosen now; record the
   components at the next planned suite rebuild.
4. **Nothing cited: `None`, not `0.0`** (requirement 10). v1's `grade_grounding` gives
   precision `0.0` to an uncited labelled case. Chosen: `None` for v2, so precision measures
   citations made and the decline and citation rates carry the rest. Recommendation: as chosen.
5. **Macro mean** (requirement 13). Chosen: the existing per-case mean ("XbrlRag's own rates
   over its H-F cases"). A pooled per-citation rate would weight a 30-filer peer case 30 times a
   lookup. Recommendation: as chosen.
6. **No grade change and no grader-version bump** (requirement 11). Grounding does not move a
   grade (v1 turns CORRECT into PARTIAL on unsatisfied grounding), so value accuracy and the
   stored grades stay comparable, and `grader_version` stays "4" (four retired specs assert it).
   Recommendation: as chosen; a later change can gate on grounding once its rate is known.
