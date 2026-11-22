# multidim-recovery — resolve multi-dimension facts the plan under-specifies or splits

Phase 5 of the accuracy plan, the remaining deterministic part
([accuracy plan §4 item 2](../design/XbrlRag_Accuracy_Plan_Oct2026.md)). `multidim-full-match`
credits a lookup whose hints name every dimension of a fact. This spec covers the two ways the
plan still fails to name them:

- **(a) A neutral consolidation member is left out.** "NVIDIA FY2025 operating income for
  Graphics Segment and Operating Segments" is fact 65496. Its dimensions are
  `StatementBusinessSegmentsAxis = Graphics Segment` and
  `ConsolidationItemsAxis = Operating Segments`. The second dimension only says "this is the
  segment-level figure"; the filer tags every segment row with it. The planner drops it, and the
  lookup then asks for `{"segment": "graphics"}`. That is a one-dimension request, so it goes to
  the flat view. The flat view keeps only the fact's *first* dimension, which here is the
  consolidation member, so the lookup finds nothing.
- **(b) One question is split into sibling lookups.** "Broadcom FY2024 revenue for Asia
  Pacific and Subscriptions and Services" is planned as two lookups:
  `{"geography": "asia pacific"}` (AMBIGUOUS) and `{"product": "subscriptions and services"}`
  (OK, but that is the product *total*, not the asked cell). One lookup carrying both hints
  resolves fact 25183 (2,203,000,000).

## Requirement

1. **Neutral member.** `ConsolidationItemsAxis = OperatingSegmentsMember` is *neutral*: a fact
   carrying it matches a lookup as if that dimension were absent, unless a hint names it.
   - A one-hint lookup that finds no row on the flat path (including the existing axis retry)
     falls back to the multi-dimension view. There it accepts a fact whose dimensions, once the
     neutral one is set aside, match the hint one-to-one.
   - Such a fact earns the dimension credit, so an exact single match is OK at confidence 0.8.
   - Other `ConsolidationItemsAxis` members (intersegment elimination, corporate non-segment,
     segment reconciling items) are **not** neutral: they change the figure's meaning.
2. **Space-insensitive containment.** Some filers have no label linkbase entry for their members,
   so the label is the raw local name ("ClientAndGamingMember"). The planner writes "client and
   gaming". The new matcher therefore also accepts a hint whose space-free form is contained in
   the label's space-free form.
3. **Intersection fallback.** After a plan has run, the executor looks for groups of **sibling
   lookups**. Siblings are `lookup` steps with a non-empty `dimensions` object that share
   `cik`, `fy`, `concept_family`, `fiscal_period` (default `"FY"`) and `ytd` (default false).
   - For each group of two or more siblings where **at least one** is not OK, it runs one more
     lookup carrying every distinct hint of the group.
   - It **accepts** the result only when the lookup is OK **and** its best candidate's
     dimensions match the merged hints one-to-one, with neutral members set aside.
   - An accepted result is appended as an extra step. The siblings' own results are never
     changed or removed. A group whose siblings all resolved OK is left alone, because those were
     separate questions ("revenue for iPhone, and for the Americas").

## Acceptance criteria

- **C1**: `is_neutral_dimension(axis, member_label)` is `True` iff both hold:
  - the axis local name (the text after the last `:`) is `ConsolidationItemsAxis`;
  - the label, normalised by `_norm_label` with all spaces removed, is `operatingsegments` or
    `operatingsegmentsmember`.

  Point cases (labels from the 2026-10-14 corpus):
  - `True`: NVIDIA "Operating Segments [Member]" (srt/2024 axis URI), AMD FY2025
    "OperatingSegmentsMember", and `srt:ConsolidationItemsAxis` with "operating segments".
  - `False`: "Intersegment Elimination [Member]", "Corporate, Non-Segment [Member]" and
    "Segment Reconciling Items [Member]" on the same axis. Also `False`: "Operating Segments
    [Member]" on `StatementBusinessSegmentsAxis`, and Micron's "Other Operating Segment
    [Member]" on either axis.

  Oracle: the definition.
- **C2**: `dimensions_match_ignoring_neutral(dimension_labels, member_hints)` is `True` iff
  all of these hold:
  - there is at least 1 hint;
  - after removing every neutral entry (C1) whose label contains **no** hint, the remaining
    labels number exactly as many as the hints;
  - a one-to-one assignment of hints to remaining labels exists in which each hint is contained
    in its label.

  "Contained" means: `_norm_label(hint)` is a substring of `_norm_label(label)`, **or** the two
  normalised forms with all spaces removed are substrings likewise. `None` or empty labels give
  `False`. Point cases:
  - NVIDIA fact 65496 with `["graphics"]`, `["graphics segment"]` and
    `["graphics", "operating segments"]`: all `True`.
  - AMD fact 115042 (`ClientAndGamingMember`, `OperatingSegmentsMember`) with
    `["client and gaming"]`: `True`.
  - Intersegment Elimination + Graphics with `["graphics"]`: `False`.
  - The two-dimension Apple fact 13665 with `["level 2"]`: `False`.

  Oracle: the definition, by hand.
- **C3**: `XbrlFactstoreLookup()._build_result(...)` for a **one-hint** lookup on a row from
  the multi-dimension view. Fact 65496, value 5,085,000,000, preferred concept, no section hint:
  - With labels {Operating Segments, Graphics Segment} and hint "graphics": status OK,
    confidence 0.8 (0.4 one value + 0.2 preferred + 0.2 dimension).
  - With labels {Intersegment Elimination, Graphics Segment}: confidence 0.6, AMBIGUOUS.

  The flat-path rule for a row without `dimension_labels` is unchanged. Oracle:
  `_score_confidence` by hand.
- **C4**: `intersection_steps(steps, results)` returns the merged lookup steps for every group
  of sibling lookups (Requirement 3) that has at least two members and at least one result whose
  status is not `StepStatus.OK`. Each merged step:
  - `id` is the siblings' ids joined with `"+"`, in plan order;
  - `op` is `"lookup"`;
  - `cik`, `fy`, `concept_family`, `fiscal_period` and `ytd` are copied from the first sibling;
  - `dimensions` is `{"member_1": h1, "member_2": h2, ...}`. These are every member value of
    the siblings' `dimensions`, in plan order, de-duplicated by `_norm_label`. The keys carry no
    axis, so no axis constraint applies.
  - A group with fewer than 2 distinct hints yields nothing.

  Steps whose `dimensions` is `"NONE"`, missing or empty, and non-`lookup` steps, are never
  siblings. Groups are returned in the order of their first sibling. Oracle: the definition.
- **C5**: `PlanExecutor.execute(plan)` with a lookup that answers per the hints it is given:
  - **Accepted:** for each merged step it calls `factstore_lookup.lookup` once. When the result
    is OK and its first candidate's `dimension_labels` satisfy C2 against the merged hints, the
    executor appends one `StepResult`: `op` `"lookup"`, status OK, the lookup's value and
    citation, `step_id` the merged id. Its `to_dict()` carries `"derived_from"` (the sibling
    ids, in plan order) and `"dimensions"` (the merged hints). `bindings[merged id]` is the
    value.
  - **Rejected:** a result that is not OK, has no candidates, or whose candidate fails C2 is not
    appended. The step list and bindings are then as without this change.
  - **Not attempted:** when every sibling is OK, `lookup` is never called with a merged step.
  - In every case the siblings' own `StepResult`s are unchanged.

  Point case: Broadcom FY2024, siblings `aprev24` `{"geography": "asia pacific"}`
  (AMBIGUOUS) and `subs24` `{"product": "subscriptions and services"}` (OK). The merged step
  `aprev24+subs24` resolves fact 25183, 2,203,000,000, with labels "Subscriptions and Services
  [Member]" and "Asia Pacific [Member]". Oracle: the definition; the values are the stored
  fact.
- **C6**: Metamorphic properties:
  - `is_neutral_dimension` and `dimensions_match_ignoring_neutral` ignore case and the order of
    hints and labels.
  - Adding a neutral dimension to a fact whose hints do not name it leaves the C2 result
    unchanged.
  - Adding a non-neutral dimension turns a `True` into `False`.
  - It is a superset: whenever `dimensions_fully_matched(labels, hints)` is `True`,
    `dimensions_match_ignoring_neutral(labels, hints)` is `True`.
  - Adding an unrelated step (another concept family) to a plan does not change
    `intersection_steps`' merged steps.

  Oracle: the definitions.

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_lookup.is_neutral_dimension(axis: str, member_label: str) -> bool`
- `src.retriever.xbrl.xbrl_factstore_lookup.dimensions_match_ignoring_neutral(dimension_labels: Mapping[str, str] | None, member_hints: Sequence[str]) -> bool`
- `src.retriever.xbrl.plan_executor.intersection_steps(steps: Sequence[dict], results: Sequence[StepResult]) -> list[dict]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `XbrlFactstoreLookup.lookup`: a one-dimension request whose flat query and axis retry return
  no rows queries `mv_financial_facts_multidim`. It keeps only rows satisfying
  `dimensions_match_ignoring_neutral` with the one hint, and records the fallback in
  `resolution_notes`. A flat hit always wins, so existing resolutions do not move.
- `XbrlFactstoreLookup._dimension_match_exact`: a candidate with `dimension_labels` is credited
  by `dimensions_match_ignoring_neutral`, for one hint or several. That rule is a superset of
  `dimensions_fully_matched` (C6). A flat candidate (no `dimension_labels`) keeps the
  `segment_label` equality rule.
- The multi-dimension row filter (`_multidim_row_matches`) must not drop a row that
  `dimensions_match_ignoring_neutral` would credit, i.e. it uses the same containment test.
- `PlanExecutor.execute` runs `intersection_steps` after the plan's own steps and executes them
  as C5 describes. It emits a `decision` event (accepted or rejected, with the merged hints) so
  the trace shows the recovery. `StepResult` gains the optional `derived_from` and
  `dimensions`; `to_dict()` emits them only when `derived_from` is set.
- `dimensions_fully_matched` itself is unchanged (it stays locked by `multidim-full-match`'s
  retired tests).

## Notes

- Evidence (2026-10-14, local `xbrl_db`, corpus `pm:38191fbfbdffeb53`). A direct lookup over
  the 50 `aq-8b` cases:
  - With every qualifier in one step, 45/50 resolve.
  - With "Operating Segments" dropped (18 cases), only 5/18 resolve, and those only because the
    flat view's first dimension happened to be the segment axis.
  - A throwaway prototype of this spec resolved 18/18 of those one-hint lookups and kept the
    all-hints result at 45/50.
- The latest traced end-to-end runs refuse most `aq-8b` cases. The causes are (a) a dropped or
  separately-looked-up "Operating Segments" (NVIDIA, AMD, Oracle, Apple, Micron), and (b) splits
  into per-qualifier lookups (Broadcom ×4, Apple Level 2 ×2, Microsoft ×5, Micron term loan).
- Facts used:
  - NVIDIA 65496: `OperatingIncomeLoss` FY2025, 5,085,000,000.
  - AMD 115042: `OperatingIncomeLoss` FY2025, 2,855,000,000; labels
    `ClientAndGamingMember`, `OperatingSegmentsMember`.
  - Broadcom 25183: revenue FY2024, 2,203,000,000.
  - Apple 13665: see `multidim-full-match`.
- The fallback cannot produce a confident answer to a *different* cell, because acceptance
  requires a one-to-one match of every hint with nothing left over except a neutral member.
- Out of scope:
  - the 4 Tesla debt cases that stay AMBIGUOUS on concept ties (Phase 3(a));
  - Amazon D&A "Early Retirement" (the lookup itself misses);
  - hints that are not members at all ("classification: long-term", "measurement: fair
    value"), which make the merged lookup fail and so are rejected;
  - the flat view matching a multi-dimension fact on its first dimension only (a known hazard;
    separate follow-up).
- Phase 3(a) evidence found while prototyping. The multi-dimension query applies `LIMIT`
  before the Python dimension filter.
  - Micron FY2024 "2027 Term Loan A" resolves today only because the limit cuts off a rank-4
    `LongTermDebtNoncurrent` fact with the same dimensions and a different value. Without that
    cut, the family-wide distinct-value check makes it AMBIGUOUS.
  - The one-hint fallback therefore filters hints in SQL itself, and the ordinary path is left
    as it is until Phase 3(a).
