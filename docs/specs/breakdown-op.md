# breakdown-op — a deterministic single-filer breakdown, and one definition of "the breakdown"

Phase 4 of the accuracy plan
([XbrlRag_Accuracy_Plan_Oct2026.md](../design/XbrlRag_Accuracy_Plan_Oct2026.md) §4).

## Requirement

"Break down Amazon's fiscal 2025 revenue by segment" is answered today by the planner guessing
member names and emitting one `lookup` per guess. In the 486-case baseline
(`eval-7f966d6da489`), breakdowns score 8 of 63 (13%). Most failures resolve 0–1 components.

The ground truth has a second problem. A filer can tag **several alternative partitions** of the
same total on one axis:

- Amazon's product axis carries goods/services (Product 296.3B + Service 420.7B) *and* product
  lines (online stores, third-party seller services, AWS, …).
- Apple's carries Product/Service *and* iPhone, Mac, iPad, Wearables, Services.

The suite builder keeps a top-5 mix of these. 14 of the 61 breakdown cases with a known total
have expected rows that sum to more than total revenue, so no coherent answer can match them.

After this change:

1. **One rule defines the breakdown.** Among the members tagged on the axis, take the subset that
   sums to the undimensioned total. When several do, take the **finest**, the one with the most
   members (Apple → its five product lines). With no such subset there is no breakdown to
   state.
2. **A new plan op, `breakdown`**, runs one deterministic query per filer-year and axis, applies
   that rule, and returns every chosen member with its own `fact_id`. No member names are
   guessed.
3. **The suite builder uses the same rule.** A breakdown case's expected rows are the whole chosen
   partition. A filer-year with no complete partition yields no case: ambiguous ground truth is
   worse than none.
4. **The eval counts a breakdown step's rows** as produced values and cited facts.

## Acceptance criteria

- **C1**: `choose_partition(members, total, rel_tol=1e-4)` takes `(label, value)` pairs and
  returns the indices (ascending) of the chosen partition, or `None`.
  - **Partition:** a subset of size ≥ 2 whose values sum to `total` within `rel_tol`, by
    `close_enough`.
  - **Choice:** the largest such subset. Ties go to the smallest absolute deviation from
    `total`, then the lexicographically smallest sorted labels.
  - **Duplicates:** identical `(label, value)` pairs count once (the first index); Apple FY2024
    tags `Service` twice.
  - **`None` when:** no subset qualifies; `total` is `None` or not positive; or there are more
    than 20 distinct members.

  Oracle: the definition. Point cases are real FY2025 values from the 2026-10-14 corpus:
  - Apple: Product/Service vs the five product lines → the five.
  - Amazon segments: North America + International + AWS = total.
  - AMD geography with one member missing (US + CN + TW + SG = 28.58B against 34.64B) → `None`.
- **C2**: `XbrlFactstoreBreakdown(db_conn_params).breakdown(step)` takes a step
  `{"id", "op": "breakdown", "cik", "fy", "concept_family", "axis"}`, with `axis` one of
  `segment` | `product` | `geography`. It returns a `ScreenResult`:
  - **OK:** `rows` are the chosen partition (C1), each with `label` (the member label without its `[Member]` marker), `value`, `unit`, `fact_id`
    (str) and `fiscal_year`, ordered by value descending and ranked 1..n.
  - **NOT_FOUND:** no members, no total, or no complete partition. The reason goes in
    `resolution_notes`, and `rows` is empty.
  - **ERROR:** an unknown `axis` or a non-`breakdown` op.

  Oracle: requirement; a fake fact store (the DB is an I/O boundary) holding the C1 point
  cases.
- **C3**: `PlanValidator().validate(plan)` accepts a plan whose step is a well-formed
  `breakdown`. It reports an error for a breakdown step missing `cik`, `fy`, `concept_family`
  or `axis`, or with an `axis` outside the three. Oracle: requirement.
- **C4**: `all_numeric_values(resp)` includes the `value` of every row of each OK `breakdown`
  step, and `all_fact_ids(resp)` each row's `fact_id`. Rows of a non-OK breakdown step count
  for neither. Oracle: requirement (the grader reads these).
- **C5**: The suite builder, for a `breakdown` template whose `expect` names a `total` column:
  - sets `expected_rows` to the whole partition `choose_partition` selects over the returned
    member rows, not cut by `top_n`;
  - sets `expected_fact_ids` to those members' fact ids;
  - emits no case when the result is `None`.

  Oracle: a fake fact store; the C1 point cases by hand.

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_breakdown.choose_partition(members: Sequence[tuple[str, float]], total: float | None, rel_tol: float = 1e-4) -> list[int] | None`
- `src.retriever.xbrl.xbrl_factstore_breakdown.XbrlFactstoreBreakdown.__init__(self, db_conn_params: dict | None = None) -> None`
- `src.retriever.xbrl.xbrl_factstore_breakdown.XbrlFactstoreBreakdown.breakdown(self, step: dict) -> ScreenResult`

Behavior on existing code and wiring (added by the implementer; a spec PR may only add stubs):

- **Queries.** `breakdown()` reads `mv_segment_matrix` for `segment`
  (`StatementBusinessSegmentsAxis`) and `product` (`ProductOrServiceAxis`), and
  `mv_geographic_facts` for `geography`. Scope: the filer, the fiscal year, `fiscal_period`
  (default `FY`, annual) and one unit. Member rows are aliased `member_label`, `value`,
  `fact_id`, `unit`. The total comes from `mv_financial_facts`, aliased `value`, `fact_id`,
  `unit`: the undimensioned current-period figure, in the members' unit. It connects via
  `psycopg2.connect(**db_conn_params)` and applies the read-time publish gate.
- **Planner and validator.**
  - `PlanValidator` adds `breakdown` to `VALID_OPS` with the C3 checks.
  - The plan-generator prompt gains the op (a new prompt **version**, per the prompt-registry
    rule): use one `breakdown` step for "break down / split / by segment / by geography" of a
    single named filer, instead of one lookup per guessed member.
- **Executor and pipeline.** `PlanExecutor` dispatches `breakdown` to an injected
  `breakdown_fn`, like `screen_fn`. `XbrlRetrievalPipeline` wires `XbrlFactstoreBreakdown` and
  maps its status as `_screen_step` does.
- **Templates.** The breakdown templates (3a, 3b, 4a, 10b) return every member plus a `total`
  column, and drop `top_n`.

## Notes

- **One rule for system and ground truth** is deliberate: the eval should measure whether the
  pipeline returns *the* breakdown, not which of several valid splits it happened to pick.
  Because a bug in `choose_partition` would then go unseen by the eval, C1 is locked against
  hand oracles.
- **Data:** per filer-year at most 12 members on ProductOrServiceAxis, 8 geographies and 5
  business segments (2026-10-14). An exhaustive search is cheap, and a coincidental subset sum
  within 1e-4 is unlikely at that size.
- **Out of scope:** multi-dimension breakdowns (Phase 5); breakdowns of non-revenue metrics
  beyond what the views hold; the corrupt AMD geo code `//www.amd.com/20251227` in the
  geography mapping, a separate data fix.
