# breakdown-reconciling-items — overlapping hierarchies, reconciling lines and eliminations in "the breakdown"

Area J of the segments/IFRS investigation (`data/eval/segments_ifrs/report.md`, 2026-11-07),
finding **J7** and its proposed criteria C5 and C6 (here C1 and C2). Builds on
[breakdown-op.md](breakdown-op.md), whose rule `choose_partition` (the finest member set summing
to the undimensioned total) is shared by the `breakdown` step and the suite's ground truth.

Against SEC's Financial Statement Data Sets the `breakdown` op agrees on 170 filer-year-axes
and states no wrong member value. It answers NOT_FOUND, though, where an analyst reading the
filing would answer:

- **A reconciling line outside the axis.** GOOGL FY2024 (0001652044-25-000014): Google
  Services 304,930 + Google Cloud 43,229 + Other 1,648 = 349,807 ($M) against total revenues
  350,018. The difference is the undimensioned hedging-gains line (211), which the filer
  presents in the same segment table (tagged `GainLossOnOilAndGasHedgingActivity`). Every year,
  and on the geography axis too.
- **Total revenues that include a non-segment line.** WMT FY2025 (0000104169-25-000021): the
  three segments' net sales sum to 674,538; total revenues 680,985 add membership and other
  income (`OtherIncome`, 6,447), a calculation child of `Revenues`.
- **Single-member axes.** UPS FY2023 tags one segment alone (Supply Chain Solutions 13,169
  against 90,958): there is nothing to state but the gap.

Two shapes need the rule made explicit rather than left to luck:

- **Overlapping hierarchies.** ORCL FY2024 geography carries a country split (US, UK,
  Germany, Japan, other) and a region split (Americas, EMEA, Asia Pacific); both are exact.
  VST FY2024 (0001692819-25-000013) product lines carry a parent "Total Other Revenues" (2,463)
  next to its own children, a 0-valued member (Intersegment Sales) and a negative row; a
  member and its own child must never both be counted.
- **Eliminations.** Consolidation items (`ConsolidationItemsAxis`: OperatingSegments,
  CorporateNonSegment, IntersegmentElimination …; ABT FY2023, UPS, VST) reconcile segments to
  the total. They must never be presented as a segment.

## Requirement

1. **One rule, extended.** `choose_breakdown` is the definition of "the breakdown". It takes
   the axis members (with each member's parents on the axis), the undimensioned total and the
   candidate reconciling lines, and returns the chosen members, at most one reconciling line,
   and the exact partitions it set aside. With plain members (distinct labels, positive values,
   no parents, no consolidation items) and no lines it chooses exactly what `choose_partition`
   chooses; the implementer re-expresses `choose_partition` through it, so there is still one
   rule (breakdown-op's graduated tests keep passing).
2. **Hierarchy.** A partition never holds a member together with one of its ancestors on the
   axis. The finest exact partition wins; the others are set aside and stated.
3. **Reconciling lines.** When no member set is exact, the complete member set plus one named
   reconciling line may make the total; the line is a row with its own `fact_id`. Otherwise
   the step is NOT_FOUND and states the gap.
4. **Eliminations and zeros** are never member rows.
5. **The step and the suite share it.** `breakdown()` builds its inputs from the fact store and
   the filing's own arcs and calls `resolve`, which calls `choose_breakdown`; the suite builder
   calls `choose_breakdown` on the template's rows.

## Acceptance criteria

All sums are compared within `rel_tol` = 1e-4 relative to the total; member and line values
are compared exactly.

- **C1**: `choose_breakdown(members, total, lines=(), rel_tol=1e-4)` chooses the finest exact
  partition, never a member together with its own ancestor, and states every partition set
  aside. Oracle: the definition below, by hand on ORCL FY2024 geography and VST FY2024 product
  (values from the filers' iXBRL, cross-checked against FSDS num; parents from the filers'
  presentation/definition arcs), and a brute-force restatement for random inputs.
  - **Inputs.** `members`: dicts with `label` (str), `value` (float | None), `member` (the
    member's local name, may be `""`) and `parents` (list of member local names; missing means
    `[]`). Other keys are ignored. `lines`: dicts with `label` and `value` (see C2).
  - **Identity.** A member's identity is `member`, or `label` when `member` is empty.
  - **Eligible members.** `value` is not None and not 0, and the member is not a consolidation
    item: its local name (after any `:`) is one of `OperatingSegmentsMember`,
    `IntersegmentEliminationMember`, `ConsolidationEliminationsMember`,
    `CorporateNonSegmentMember`, `MaterialReconcilingItemsMember`,
    `SegmentReconcilingItemsMember`, or contains `Elimination` or `Intersegment` (any case).
    Eligible members with the same (`label`, `value`) count once (the first index).
  - **Ancestors.** The ancestors of a member are its `parents`, their `parents`, and so on,
    followed through every given member with that identity (cycles end the walk).
  - **Valid set.** A set of eligible distinct members in which no two share an identity and no
    member's identity is an ancestor of another member in the set.
  - **Exact partition.** A valid set of at least 2 members whose values sum to `total` within
    `rel_tol × total`.
  - **Choice.** The exact partition with the most members; ties go to the smaller absolute
    deviation from `total`, then the lexicographically smaller sorted labels, then the smaller
    ascending index tuple. Result: `{"members": <ascending indices>, "line": None,
    "set_aside": [...]}`.
  - **Set aside.** Every other exact partition that is not a subset of the chosen one and not
    a proper subset of another such partition, ordered by the choice key, each as ascending
    indices. ORCL FY2024: the five countries are chosen and the three regions set aside.
  - **None** when `total` is None or not positive, when more than 20 distinct eligible members
    remain, or when neither C1 nor C2 finds a result.
  - `member_parents(members, arcs)` gives each member local name in `members` the sorted
    distinct parents it has among `members` in any `presentationArc` or `definitionArc` of
    `arcs` (dicts with `role`, `arc`, `parent`, `child`, `weight`); a member with none maps to
    `[]`. VST FY2024: the five children of `TotalOtherRevenuesMember`.
- **C2**: when no exact partition exists, a complete member set plus one named reconciling line
  making the total is the breakdown; otherwise the result is NOT_FOUND naming the gap; an
  elimination is never a member row. Oracle: GOOGL FY2024 segments and geography (hedging
  line 211), WMT FY2025 and FY2023 (other income 6,447 and 5,408), UPS FY2023 (gap 77,789),
  ABT FY2023 (consolidation items), by hand from the filers' iXBRL and FSDS; brute force for
  random inputs.
  - **Complete set.** A valid set of at least 2 members to which no further eligible distinct
    member can be added keeping it valid.
  - **Lines.** `lines` entries with `value` not None and not 0, the same (`label`, `value`)
    counting once (the first index).
  - **Choice.** Only when C1 found no exact partition: among (complete set, line) pairs whose
    member values plus the line's value sum to `total` within `rel_tol × total`, the one with
    the most members, then the smaller deviation, then the smaller sorted member labels, then
    the smaller line label, then the smaller line index, then the smaller index tuple. Result:
    `{"members": ..., "line": <index into lines>, "set_aside": <the member sets of the other
    pairs, distinct and not equal to the chosen set, in key order>}`.
  - `XbrlFactstoreBreakdown().resolve(step, facts)` turns one filer-year-axis into a
    `ScreenResult`. `facts` is `{"total": {"value", "fact_id", "unit", "concept"} | None,
    "members": [{"label", "value", "fact_id", "unit", "member", "parents"}], "lines":
    [{"label", "value", "fact_id", "unit", "concept"}]}`.
    - **ERROR** for a step whose `op` is not `breakdown` or whose `axis` is not one of
      `segment` / `product` / `geography`, as `breakdown()` does.
    - **NOT_FOUND** with empty `rows` when there is no total, no member with a value, or
      `choose_breakdown` returns None. `disclosure["gap"]` is `total` minus the sum of the
      distinct eligible members that have no ancestor among the distinct eligible members, and
      `resolution_notes` contain it formatted as `f"{gap:,.0f}"`.
    - **OK**: `rows` are the chosen members ordered by value descending, each with `label`
      (without its ` [Member]` marker), `value`, `unit`, `fact_id` (str), `fiscal_year` (the
      step's `fy`), `rank` 1..n and `kind` `"member"`; then, when a line was chosen, one row
      with the line's `label`, `value`, `unit`, `fact_id` (str), `fiscal_year`, `rank` None and
      `kind` `"reconciling"`. The rows sum to the total within `rel_tol`.
      `disclosure` is `{"total": {"value", "fact_id" (str), "unit"}, "reconciling": {"label",
      "value", "fact_id" (str)} | None, "set_aside": [<labels of each set-aside partition,
      value descending>], "gap": None}`; `resolution_notes` name the line's label and every
      set-aside label.
- **C3**: `reconciling_candidates(total, member_concepts, axis, arcs, facts)` lists the lines a
  breakdown may use, from the filing's own linkbase. Oracle: the GOOGL, WMT and VST
  presentation and calculation linkbases read by hand; brute force for random inputs.
  - `total` is the total fact (`concept` is its local name, `fact_id`); `member_concepts` the
    local names of the members' concepts; `axis` the axis local name
    (`StatementBusinessSegmentsAxis`, `ProductOrServiceAxis`, `StatementGeographicalAxis`);
    `arcs` as in C1; `facts` are the filing's facts in the total's period and unit, each
    `{"fact_id", "concept", "label", "value", "unit", "axis", "member"}`, `axis`/`member` None
    for an undimensioned fact (for a consolidation-item fact `label` is the member's label).
  - **Table roles.** The roles in which some `presentationArc` has `axis` as parent or child and
    some `presentationArc` has the total's concept as child.
  - **Calculation children.** For each `calculationArc` with the total's concept as parent, its
    child and `weight` (the first arc wins when a child repeats).
  - A fact is a candidate when its value is not None and not 0, its `fact_id` is not the
    total's, and either
    - it is undimensioned, its concept is neither the total's nor in `member_concepts`, and
      the concept is a calculation child (value = weight × fact value) or a `presentationArc`
      child in a table role (value = fact value); or
    - its `axis` is `ConsolidationItemsAxis`, its concept is the total's, and its member is not
      `OperatingSegmentsMember` (value = fact value).
  - Result: one `{"label", "value", "fact_id", "unit", "concept"}` per candidate fact, in the
    order of `facts`.
- **C4**: the suite builder's breakdown ground truth follows `choose_breakdown`, and
  `choose_partition` is its plain case. Oracle: GOOGL FY2024, ORCL FY2024 and UPS FY2023 by
  hand; brute force for random rows.
  - For a `breakdown` template whose `expect` names `total`, optional `expect` keys `line` (a
    boolean column marking reconciling-line rows), `member` (the member local name column) and
    `parents` (a column holding a list of parent names) feed `choose_breakdown`; without them
    every row is a member with `member` `""` and no parents.
  - `expected_rows` are the chosen member rows and, when chosen, the line row (each `entity`
    = the cleaned label, `value`); `expected_fact_ids` their fact ids. No result, no case.
  - For plain members and no lines, `choose_partition(pairs, total, rel_tol)` equals
    `choose_breakdown(...)["members"]`, or None when that is None.

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_breakdown.choose_breakdown(members: Sequence[dict], total: float | None, lines: Sequence[dict] = (), rel_tol: float = 1e-4) -> dict | None`
- `src.retriever.xbrl.xbrl_factstore_breakdown.member_parents(members: Sequence[str], arcs: Sequence[dict]) -> dict[str, list[str]]`
- `src.retriever.xbrl.xbrl_factstore_breakdown.reconciling_candidates(total: dict, member_concepts: Collection[str], axis: str, arcs: Sequence[dict], facts: Sequence[dict]) -> list[dict]`
- `src.retriever.xbrl.xbrl_factstore_breakdown.XbrlFactstoreBreakdown.resolve(self, step: dict, facts: dict) -> ScreenResult`

Behaviour on existing code (added by the implementer; the spec PR only adds stubs):

- **`choose_partition`** becomes the plain case of `choose_breakdown` (C4). breakdown-op's
  graduated tests must keep passing.
- **`breakdown()`** reads the total and the members as today, plus each member's local name,
  the filing's presentation/definition/calculation arcs and the candidate facts (undimensioned
  and single-dimension `ConsolidationItemsAxis` facts of the total's filing, period and unit),
  builds `facts` with `member_parents` and `reconciling_candidates`, and returns `resolve(step,
  facts)`. breakdown-op's fakes answer every `mv_financial_facts` query with the total row and
  every unknown query with no rows; that total row is never a line (it is the total's own
  fact), and an empty arc list yields no parents and no lines.
- **Pipeline and synthesizer.** `_breakdown_step` carries the reconciling row and the
  disclosure to the synthesizer, which states the line by name; the eval counts the line row
  as a produced value and a cited fact (breakdown-op C4 counts every row).
- **Templates.** The breakdown templates (3a, 3b, 4a, 10b) add the line rows (the same
  candidates as C3) and the `member`/`parents` columns, then the suite is rebuilt and diffed.

## Notes

- **Fixtures** (`test/spec/breakdown-reconciling-items/fixtures/`, retrieved 2026-11-07): one
  JSON excerpt per filing, read-only from `xbrl_db`, with the accession, EDGAR URL, the
  SHA-256 of every filer document used, and the FSDS quarter (URL, zip SHA-256 from
  `data/cache/sec_fsds/raw/manifest.json`) with its `num` rows for the same period. Every
  member, total and candidate value carries the filer's own `ix:nonFraction` element; the
  tests check each stored value against it. FSDS omits some of them (GOOGL's hedging line,
  ORCL's country rows), so the iXBRL element is the primary oracle and FSDS the cross-check
  where it has the row.

  | Fixture | Accession | Form | FSDS |
  |---|---|---|---|
  | `googl_fy2024.json` | 0001652044-25-000014 | 10-K | 2025q1 |
  | `wmt_fy2025.json` | 0000104169-25-000021 | 10-K | 2025q1 |
  | `wmt_fy2023.json` | 0000104169-23-000020 | 10-K | 2023q1 |
  | `orcl_fy2024.json` | 0000950170-24-075605 | 10-K | 2024q2 |
  | `vst_fy2024.json` | 0001692819-25-000013 | 10-K | 2025q1 |
  | `ups_fy2023.json` | 0001090727-24-000008 | 10-K | 2024q1 |
  | `abt_fy2023.json` | 0001628280-24-005348 | 10-K | 2024q1 |

- **Why only complete sets with a line.** With a line, any subset could be completed by some
  line item of the segment table (operating income, costs …): about 2^n × lines chances of a
  coincidental match within 1e-4. Requiring every eligible member (or a complete level of the
  hierarchy) leaves one or a few sums per line.
- **Tolerance makes tiny members optional.** VST's −1 member is inside 1e-4 of 17,224; the
  finest rule keeps it, and the subsets without it are not "set aside" (they are subsets of a
  stated partition).
- **VST nuclear and solar.** The filer presents the nuclear (545) and solar (11) credits under
  the product domain in its government-assistance note, not under "Transferable Production Tax
  Credit Revenues (Nuclear And Solar Assets)" (556). By the arcs they are not its children, so
  the finest partition uses them in place of 556; the partition with 556 is set aside.
- **Out of scope:** the geography code join and share-of-total views (`segment-geo-views`),
  one-dimension lookups and screens (`segment-lookup-screens`), multi-dimension cells as
  breakdown members (ABT's and UPS's segment figures are segment × consolidation-item cells),
  and IFRS axes.
- **Held-out data:** none read; every fixture filer is a corpus development filer.
