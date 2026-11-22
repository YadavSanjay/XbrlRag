# filer-rank-coverage — a filer's rank 1 is the concept it uses most consistently

Follow-up to [`concept-rank-integrity`](concept-rank-integrity.md), which fixed rank-1
*meaning*. Its per-filer rule passed `filer_ranking` every eligible concept the filer reports
**in any year**, so one stray year decides the override.

NVIDIA tags total revenue as `RevenueFromContractWithCustomerExcludingAssessedTax` (the global
rank 1) only in FY2022, and as `Revenues` in FY2023–FY2026. FY2022 alone suppressed the
override, so NVIDIA's revenue from FY2023 to FY2026 resolves at rank 2 and is refused. The full
run `eval-a42931beca30` (`5b3dc6c`, after the reload) measured:

- 79.0% against 81.8% for `eval-b99a265be9f0` on the same suite;
- 25 CORRECT → REFUSED, silent-wrong still 0;
- the losses concentrated in NVIDIA revenue (lookups, segment members, multi-dimension), Meta
  interest expense and Broadcom net income.

## Requirement

The set handed to `filer_ranking` as *reported* is the filer's **most consistently used**
eligible concepts: those reported in the greatest number of its fiscal years. `filer_ranking`
then takes the first of them in global rank order. In effect the winner is the concept with the
most years, with ties broken by global rank. Components stay ineligible, so the meaning
guarantee of `concept-rank-integrity` is unchanged.

Measured over every (filer, family, fiscal year) in the corpus, counting the years whose
current undimensioned figure the filer's rank 1 does **not** carry while another eligible
concept does:

| Rule | Misses |
|---|---|
| any year (merged) | 21 |
| latest year only | 14 |
| **most years, ties by global rank** | **4** |

## Acceptance criteria

- **C1**: `consistent_concepts(years_reported, eligible)` returns the QNames in `eligible`
  whose set of years in `years_reported` is largest. It returns every QName tied at that size,
  and an empty set when no eligible QName has any year. QNames outside `eligible` never count.
  Point cases:
  - NVIDIA `Revenue`: `{RevenueFromContract…: {2022}, Revenues: {2022, 2023, 2024, 2025, 2026}}`
    gives `{Revenues}`.
  - A tie, `{A: {2024, 2025}, B: {2024, 2025}}`, gives `{A, B}`.
  - A component with more years than any primary (`Depreciation` in 5 years against
    `DepreciationDepletionAndAmortization` in 2) is excluded by `eligible`, so the primary
    wins.

  Oracle: the definition.
- **C2**: Composed with `filer_ranking` (locked by `concept-rank-integrity`), NVIDIA's
  `Revenue` ranking puts `Revenues` at rank 1. When every eligible QName ties, the composition
  returns `None`, because the global rank 1 is among them.

  Oracle: the definition and `filer_ranking`'s rule, by hand.
- **C3**: Properties of `consistent_concepts`:
  - the result is a subset of `eligible`;
  - every member has the same, maximal, year count;
  - adding a year to a non-member that is already at the maximum count makes it the only
    member;
  - the result does not depend on iteration order.

  Oracle: the definition.

## Interface

New function (stubbed in the spec PR):

- `src.ingest.sec_edgar.ingest.layer0_populator.consistent_concepts(years_reported: Mapping[str, Collection[int]], eligible: Collection[str]) -> set[str]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `populate_concept_mapping_overrides` collects, per filer, the fiscal years in which each QName
  has an undimensioned numeric fact. A year means the canonical annual filing's
  `fiscal_year`. It passes `consistent_concepts(...)` over the family's eligible QNames to
  `filer_ranking` as `reported`.
- Nothing else changes, and `filer_ranking` is untouched.

## Notes

- **Reload after the merge:** only the override step and what follows it — step 4 (overrides),
  step 5 (namespace expansion) and the MV rebuild of the `concept-rank-integrity` runbook. The
  generic CSV is unchanged.
- **The remaining 4 misses** are left as they are: NVIDIA dividends per share FY2022, Broadcom
  restructuring FY2024–25, and Alphabet revenue FY2025. In each, a filer changed concepts in one
  or two years. Fixing those needs a per-year override, which `concept_mapping` cannot express
  (its overrides are keyed by filer only).
