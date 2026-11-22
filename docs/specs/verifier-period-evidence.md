# verifier-period-evidence — a value step's fiscal year and period are verifier evidence

Follows [verifier-derived-disclosures](verifier-derived-disclosures.md) (its review
point 3 named this gap) and [period-year-length-answers](period-year-length-answers.md).

The faithfulness verifier judges each statement against the evidence `_resolve_fact_evidence`
shows it (`src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py`). Since verifier-derived-disclosures, a screen row
carries its `fiscal_year`, `rank` and `period_end`, and a cited screen its `screen_summary`.
The other value steps carry no period at all:

- a **lookup** step's evidence is `value`, `unit`, `citation` (`fact_id`, accession, anchor,
  section). Its fiscal year is only in the plan step (`fy`); its period start and end are only
  on the cited candidate of the lookup's detail (`plan_executor.cited_period`), which
  `StepResult.to_dict()` does not emit;
- a **compute** step's evidence is its value and unit; which lookups it combines, and their
  years, are not shown;
- an **aggregate** step's evidence is `fn`, `n`, `members`, `fact_ids`, `middle`, `universe`,
  `universe_members`, `exclusions`. The aggregate dict also holds `period_ends` and
  `period_ends_differ`, but the verifier filters them out, and the fiscal year is on the screen
  it aggregates (`disclosure["fiscal_year"]`), not on the aggregate.

**The defect.** Nearly every answer names its fiscal year ("The median total current assets of
the semiconductor companies in fiscal 2023 was 21,244,000,000 USD"), and the verifier, seeing no
year, marks the statement `PARTIALLY_SUPPORTED` ("the aggregate evidence does not specify fiscal
2023"). A partial verdict keeps the statement but forces `metadata["fully_answered"] = False`, so
the answer is reported as incomplete when it is right. Measured on the 2026-11-12 verifier slice
(58 dev cases, DeepSeek, `eval-a887bbc1df30` control at 26a8b71 / `eval-d789be154ba8` at
8109deb): after verifier-derived-disclosures, 27 of the 30 remaining partial verdicts give a missing fiscal year or
period as the reason, and 34 of 57 answers are flagged `fully_answered = False` (47 of 58
before verifier-derived-disclosures). The eval reads `fully_answered = False` as **hedged**
(`src/eval/systems/xbrlrag_full.py::_refusal_signals`), and hedged changes grading: it exempts a
wrong answer from the silent-wrong count, and it turns on the declined-aggregate rule
(grading-dont-care-and-ties requirement 4), which removed the correct median from
`int-bd7ee7bd1bce-0231b50a` and graded it `HEDGED_MISS` (see grading-stated-aggregate).

**The general rule** (as in period-year-length-answers and verifier-derived-disclosures): what the code knows about a cited step is its
evidence. A lookup's period comes from the fact it cites; a compute's from the lookups it
combines; an aggregate's from the screen it aggregates. All are derived in code, never by an
LLM, and shown to the verifier so a true year or period claim is `SUPPORTED` and a wrong one
still is not.

A wrong partial verdict goes unnoticed (the answer reads well, and the flag only shows in the
hedge rate and the silent-wrong exemption), and the right period is stated independently by the
cited facts. Route: locked spec.

## Requirement

### 1. A lookup's period (`lookup_period`)

`lookup_period(plan_step, period_start, period_end)` takes the lookup's plan step and the
**stored** start and end of its cited fact (`cited_period(result)`; the view stores Arelle's
next-midnight end, one day after the period's last day). It returns a new dict with keys exactly
`fiscal_year`, `fiscal_period`, `ytd`, `period_start`, `period_end`:

- `fiscal_year`: `plan_step["fy"]` when it is an int and not a bool, else None;
- `fiscal_period`: `plan_step["fiscal_period"]` when it is a non-empty str, else `"FY"`;
- `ytd`: `plan_step.get("ytd") is True`;
- `period_start`: the stored start as an ISO `YYYY-MM-DD` str (a date or a str accepted), None
  when None (an instant fact has no start);
- `period_end`: the **stated** last day, `screen_disclosure.stated_period_end(period_end)`
  (one day before the stored end), None when None.

It returns None when `fiscal_year`, `period_start` and `period_end` would all be None. The plan
step is never mutated.

### 2. A compute's period (`compute_period`)

`compute_period(inputs)` takes the compute's referenced steps in the order the expression first
references them, as `(step_id, period)` pairs, where `period` is that step's period (Requirements
1-3) or None. It returns None when no input has a period, else a new dict with keys exactly
`inputs` and `fiscal_years`:

- `inputs`: one dict `{"step_id": step_id, **period}` per input whose period is not None, in
  order. An input's period that itself has an `inputs` list (a compute over a compute) is
  replaced by the entries of that list, in their order (flattened, recursively); a `step_id`
  already listed is not listed again (the first wins);
- `fiscal_years`: the sorted distinct ints (not bools) among the listed entries' `fiscal_year`.

Entries are copies: nothing returned aliases an input's dicts.

### 3. An aggregate's period (`aggregate_period`)

`aggregate_period(screen_step, aggregate)` takes the screen step the aggregate's `of` names (as
`StepResult.to_dict()` holds it) and the aggregate step's `aggregate` dict. It returns None when
`screen_step` is not a mapping whose `op` is `"screen"`. Otherwise a new dict with keys exactly
`fiscal_year`, `period_ends`, `period_ends_differ`:

- `fiscal_year`: the screen's `disclosure["fiscal_year"]` when the disclosure is a mapping and
  that value is an int (not a bool); else the rows' `fiscal_year` when every row has the same
  int; else None;
- `period_ends`: a dict copy of `aggregate["period_ends"]` (`{}` when absent or None);
- `period_ends_differ`: `bool(aggregate.get("period_ends_differ"))`.

### 4. What the verifier LLM sees

A step dict may carry `period` (a mapping, set by the executor, Wiring 1). For each statement
sent to the LLM, every `fact_evidence` entry whose step is a `lookup`, `compute` or `aggregate`
with a mapping `period` carries the key `period`, equal to a copy of that step's `period`. A
step without one has no `period` key. A screen or breakdown row entry is unchanged (it already
carries `fiscal_year` and, for a screen, `period_end`). Every other key, the batching, the
deterministic checks and the verdict handling are unchanged.

### 5. Wiring (not locked; implementer)

1. **Executor.** `StepResult` gains `period: dict | None = None`, emitted by `to_dict()` when
   not None. An OK lookup gets `lookup_period(plan_step, *cited_period(result))`; an OK compute
   gets `compute_period` over its referenced steps (`XbrlCalculatorTool.referenced_step_ids`,
   each step's own `period`); an OK aggregate gets `aggregate_period(resolved[of].to_dict(),
   agg)`. A non-OK step gets none. A lookup answered by a fallback (sub-family, intersection,
   derived gross profit, sum of cells) gets the period of the fact it cites, or none when it
   cites no single candidate (`cited_period` returns `(None, None)`; `fiscal_year` from the plan
   still applies). Compound answers: each sub-query's steps carry their own.
2. **Verifier.** `_resolve_fact_evidence` copies `period` into the entry (Requirement 4).
3. **Prompt.** New `_base/faithfulness_verifier/v3` (v2 unchanged; registry default → v3): v2
   plus one sentence: a lookup, compute or aggregate entry may carry `period` (fiscal year,
   fiscal period, period start and end; for a compute, each input's; for an aggregate, the
   screen's fiscal year and each member's period end), derived in code from the cited facts; a
   claim of a fiscal year or period is SUPPORTED when it matches. User template and response
   format unchanged.
4. **Docs.** `src/retriever/xbrl/CLAUDE.md` "Citations are checked deterministically" bullet
   and the verifier module docstring.
5. **Unit tests** (`test/unit/`): the executor sets `period` on an OK lookup, compute and
   aggregate and not on an ERROR step; `to_dict()` emits it; the registry resolves v3; v3's
   placeholders match `_build_messages`.
6. **Measure** on the 2026-11-12 slice (`data/eval/slices/verifier_disclosures_2026-11-12.json`,
   DeepSeek, control vs candidate, same day): partial verdicts per answer, the share of answers
   with `fully_answered = False`, and grades.

## Acceptance criteria

Fixtures are hand-built plan steps and `StepResult.to_dict()` steps. The real values are from the
stored 2026-11-12 slice traces (read-only `audit.query_audit`, `xbrl_db`; public SEC XBRL facts):
the semiconductors' FY2023 current-assets screen `ca_screen` (INTC 154795 43,269,000,000 period
end 2023-12-30, NVDA 63024 23,073,000,000 2023-01-29, QCOM 177873 22,464,000,000 2023-09-24, MU
55674 21,244,000,000 2023-08-31, AVGO 24737 20,847,000,000 2023-10-29, AMD 19187 16,768,000,000
2023-12-30, TXN 182004 15,122,000,000 2023-12-31; universe `semiconductors`, 8 members, TSM
excluded for currency TWD, fiscal year 2023, ends differ) with its median `ca_median`
(21,244,000,000, middle MU), and Apple's FY2025 current ratio (lookup `ca` fact 14482
152,987,000,000 and lookup `cl` fact 14504 176,392,000,000, both instants, compute `cur_ratio`
= ca / cl). Apple's FY2025 balance-sheet date is 2025-09-27, stored as 2025-09-28. The verifier
is the real one; its LLM is a scripted adapter behind the real gateway (the only fake). Nothing
reads `data/`, a database or the clock.

- **C1**: `lookup_period` (Requirement 1). Apple `ca` (plan `fy` 2025, no `fiscal_period`,
  stored end "2025-09-28", no start) → `{"fiscal_year": 2025, "fiscal_period": "FY", "ytd":
  False, "period_start": None, "period_end": "2025-09-27"}`. A duration (start "2022-09-25",
  stored end "2023-10-01") → start "2022-09-25", end "2023-09-30". A Q2 YTD plan step keeps
  `"Q2"` and `ytd` True. `fy` None or a bool with no dates → None; `fy` None with dates → a
  dict with `fiscal_year` None. A `datetime.date` end is accepted. Oracle: Requirement 1 and the
  one-day stated-end rule, by hand. Properties: for any date d, `period_end` is d minus one day;
  the plan step is never mutated.
- **C2**: `compute_period` (Requirement 2). `cur_ratio` over `ca` and `cl` (both C1-shaped,
  fiscal year 2025) → two entries in that order, `fiscal_years` `[2025]`. A growth compute over
  FY2024 and FY2023 lookups → `fiscal_years` `[2023, 2024]`. An input with period None is left
  out; all None → None. A compute over a compute is flattened and de-duplicated by `step_id`.
  Oracle: Requirement 2 by hand. Metamorphic: repeating an input never changes the result;
  permuting inputs permutes `inputs` and never changes `fiscal_years`; no returned dict is an
  input object.
- **C3**: `aggregate_period` (Requirement 3). `ca_screen` and `ca_median`'s aggregate →
  `{"fiscal_year": 2023, "period_ends": {…the seven ends}, "period_ends_differ": True}`. A screen
  without a disclosure whose rows all have `fiscal_year` 2023 → 2023; rows of mixed years → None.
  A lookup step as `screen_step` → None. Oracle: Requirement 3 by hand. Property: the result
  never aliases the aggregate's `period_ends`; mutating the result leaves both inputs unchanged.
- **C4**: what the LLM sees (Requirement 4). From the prompt the fake adapter received: the
  statement "The median total current assets of the semiconductor companies in fiscal 2023 was
  21,244,000,000 USD." citing `ca_median` has an entry with `period` equal to C3's dict; "Apple's
  current ratio for fiscal 2025 was 0.87." citing `cur_ratio` has `period` equal to C2's; a
  statement citing the lookup `ca` has C1's; a screen row entry (citing fact 55674) has no
  `period` key; a step without `period` gets none. The verdicts the fake returns are applied as
  before (a statement the fake calls SUPPORTED is kept, and with every statement SUPPORTED
  `fully_answered` is not forced False). Oracle: Requirement 4 and C1-C3 by hand. Metamorphic:
  permuting or repeating a statement's `fact_ids` never changes the set of `(step_id, period)`
  pairs it is shown.

## Interface

New module `src/retriever/xbrl/step_period.py` (pure; no I/O; `period_evidence.py` is taken by
the yearless-period-mention rule):

- `lookup_period(plan_step: Mapping[str, Any], period_start: dt.date | str | None, period_end: dt.date | str | None) -> dict[str, Any] | None`
- `compute_period(inputs: Sequence[tuple[str, Mapping[str, Any] | None]]) -> dict[str, Any] | None`
- `aggregate_period(screen_step: Mapping[str, Any] | None, aggregate: Mapping[str, Any]) -> dict[str, Any] | None`

Changed, not locked: `StepResult` (`period` field), `PlanExecutor` (sets it),
`XbrlFaithfulnessVerifier._resolve_fact_evidence`, prompt `_base/faithfulness_verifier/v3`.

## Notes

- **Evidence ($0, read-only).** The verifier `llm_call` responses of the two slice runs above
  (`audit.query_event`, stage `verifier`): candidate 30 `PARTIALLY_SUPPORTED` verdicts, 27 with a
  reason naming the fiscal year or period; control 102, 96 of them. The other partial reasons are
  compute operands not identified ("the evidence does not identify the operands as Microsoft
  Inventory and the median"), which `compute_period`'s `inputs` (step ids and years) partly
  addresses and the input steps' own evidence, cited alongside, carries the rest.
- **Which grades move.** Few directly: the partial verdict keeps the statement. The effect is on
  `fully_answered` (the hedge flag), hence on the silent-wrong exemption and on the
  declined-aggregate rule, and on the "(Note: …)" caveats in answers. Held-out sets were not read.
- **Not here:** narrative evidence; breakdown steps (rows already carry `fiscal_year`); the
  number check (a stated fiscal year is not an amount, `src/numerics/stated_numbers.py` already
  exempts years); cagr (its evidence already carries `start_fy` / `end_fy`).

## Review points (decide before implementing)

1. **Stated, not stored, end dates.** `period_end` is the period's last day, as screen rows and
   disclosures state it. Recommendation: keep; a stored next-midnight date would contradict the
   answer's own sentence by one day.
2. **Plan `fy` as the lookup's fiscal year.** The fiscal year shown is the one the lookup asked
   for and answered (S1 keeps the year's own column), not re-derived from the date.
   Recommendation: keep; deriving the year from a date is the bug fiscal-year-ended-date fixed.
3. **Prompt v3.** As in verifier-derived-disclosures, the prompt sentence is wiring and is measured on the slice.
