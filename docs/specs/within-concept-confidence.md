# within-concept-confidence — judge "one value" within the winning concept (Phase 3(a))

Phase 3(a) of the accuracy plan ([§4 item 3](../design/XbrlRag_Accuracy_Plan_Oct2026.md)).

`_score_confidence` gives its +0.4 "one distinct value" credit only when every candidate at the
latest period, across the **whole concept family**, has the same value. A family holds several
genuinely different measures:

- `NetIncome` holds `NetIncomeLoss` and `ProfitLoss`;
- `CashAndEquivalents` holds cash, and cash plus short-term investments;
- `DepreciationAndAmortization` holds the total and its components.

So a correctly resolved rank-1 fact is refused whenever a lower-ranked neighbour has a
different value. After `concept-rank-integrity` and `filer-rank-coverage`, rank 1 is
meaningful and free of ties, so the credit can be judged within the winning concept.

## Requirement

A lookup earns the "one value" credit when both hold:

1. all latest-period candidates of the **winning concept** (same local name as the best
   candidate; dated namespaces of one concept count as one) share one value; and
2. no candidate of **another** concept *disagrees* — has a value outside that one — where it
   is either:
   - **at the same preference rank** as the winner (the tie guard); or
   - **in the component band (50–99)** of a family whose components are separate questions.
     Today that is only `Debt`: its components (current portion, short-term borrowings, notes
     payable) are what "debt current"-style questions ask for. The planner cannot name them,
     because no such family exists.

Everything else in the confidence formula is unchanged:
- the rank-1, dimension and section credits;
- the 0.70 threshold;
- the AMBIGUOUS rule for different values *within* the winning QName.

**Ground truth.** The lookup answers an effective tax rate with the filer's **reported**
(tagged) rate. `mv_derived_metrics.EffectiveTaxRate` must therefore use that tagged rate when
the filer reports one, and fall back to `TaxExpense / PretaxIncome` only when it doesn't (one
definition for system and ground truth; maintainer decision, 2026-10-15).

## Acceptance criteria

- **C1**: `one_value_credit(winner, winner_rank, current, components_block)`. `current` is a
  list of `(concept, preference_rank, value)` for the latest-period candidates. It returns
  `True` iff both hold:
  - the non-`None` values of the entries whose local name (text after the last `:`) equals
    `winner`'s form exactly one value `v`;
  - no other-local-name entry with a non-`None` value `≠ v` has `rank == winner_rank`, or
    (when `components_block`) `50 ≤ rank < 100`.

  Point cases (2026-10-15 corpus):
  - Oracle FY2024 net income: `NetIncomeLoss` (1) 10,467M and `ProfitLoss` (2) 10,653M give
    `True`.
  - A tie: `NetIncomeLoss` (1) and `ProfitLoss` (1) with different values give `False`.
  - Two dated namespaces of `NetIncomeLoss` with one value give `True`; with two values,
    `False`.
  - Amazon FY2024 D&A: `DepreciationDepletionAndAmortization` (1) 52,795M, `Depreciation` (50)
    32,100M and `AmortizationOfIntangibleAssets` (51) 838M give `True` with
    `components_block=False`.
  - Tesla FY2024 debt: `LongTermDebt` (1) 434M and `DebtCurrent` (50) 54M give `False` with
    `components_block=True` and `True` without it.
  - An IFRS rival at rank 100 with another value does not block a rank-1 winner.
  - No non-`None` value for the winner gives `False`.

  Oracle: the definition.
- **C2**: `XbrlFactstoreLookup()._build_result(...)`:
  - Oracle FY2024 `NetIncome` rows (flat, no dimensions, no section hint): **OK, confidence
    0.8** (0.4 + 0.2 rank 1 + 0.2 no dimensions). Today it is 0.4, AMBIGUOUS.
  - Tesla FY2024 `Debt` multi-dimension rows, with hints "energy asset backed notes" and
    "nonrecourse debt": **AMBIGUOUS, confidence 0.4**. The component guard holds.

  Oracle: `_score_confidence` by hand.
- **C3**: Properties of `one_value_credit`:
  - the order of `current` does not matter;
  - adding a rival of another concept at a worse rank in the primary tier (2–49, or 100+ when
    the winner is below 100) never turns `True` into `False`;
  - adding a same-rank rival of another concept with a new value turns `True` into `False`;
  - `components_block=True` is never more permissive than `False`.

  Oracle: the definition.

## Interface

New function (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_lookup.one_value_credit(winner: str, winner_rank: int | None, current: Sequence[tuple[str, int | None, float | None]], components_block: bool) -> bool`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **`_score_confidence`** awards the +0.4 credit by `one_value_credit`, with
  `components_block = concept_family in {"Debt"}`. That set is a named module constant whose
  comment gives the reason. `_build_result` passes the family through.
- **`scripts/ddl/mv/34_mv_derived_metrics.sql`**: `EffectiveTaxRate` is the filer's tagged
  `EffectiveTaxRate`-family fact when present, else `TaxExpense / PretaxIncome`. The formula
  text and the fact ids say which one was used.
- **After merge:** the maintainer rebuilds the MVs, then the suite is rebuilt and diffed.

## Notes

- **Replay** of all 540 lookup steps of `eval-63009f24b122` through a prototype of the rule
  with the tie guard and without the Debt guard: 35 AMBIGUOUS steps become OK.
  - 15 match their case's expected value: TSMC and Oracle net income, AMD pretax income, cash
    ×3, share-based compensation ×3, effective tax rate, and the Micron term loan.
  - 5 are D&A inputs to EBITDA, and they are the right totals: Amazon's 52,795M plus its
    operating income gives the expected EBITDA.
  - 12 are net-income inputs to YoY computes.
  - 3 are effective tax rates where the tagged rate is rounded. Only Broadcom FY2025 (−0.017
    against a derived −0.01747) misses `aq-7a`'s 1% tolerance; the ground-truth change above
    resolves it.
- **Measured hazard behind the Debt guard.** With the prototype, Tesla FY2024 "debt current for
  Energy Asset Backed Notes and Nonrecourse Debt" became **OK, 434,000,000
  (`LongTermDebt`)**. The expected answer is 54,000,000 (`DebtCurrent`), and today the lookup
  refuses. The guard keeps the refusal.
- **Follow-up.** A `DebtCurrent` family would let such questions resolve, which needs planner
  vocabulary and a concept_mapping reload. Out of scope here.
