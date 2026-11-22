# rounded-duplicates — a fact tagged again at lower precision is one value, not two

An iXBRL filing often tags the same number twice: once from the statement or note table
(`decimals="-6"`, in millions) and once from the narrative ("$20.8 billion", `decimals="-8"`).
The lookup judges ambiguity on raw values within the winning concept, so it sees two distinct
values and returns AMBIGUOUS. The step's value is then unusable, and the answer is a refusal.

- NVIDIA FY2026 `Goodwill`: 20,832,000,000 (`decimals=-6`, balance sheet) and 20,800,000,000
  (`decimals=-8`).
- Amazon FY2024 `IncomeTaxExpenseBenefit`: 9,265,000,000 (`-6`, four facts) and 9,300,000,000
  (`-8`).

Across the corpus (2026-10-16), there are 110 groups of undimensioned, current-period facts
in one canonical annual filing that share a concept, period and unit but have more than one
value. In **100** of them, every value is the most precise value rounded to its own
`decimals`. XBRL 2.1 §4.6.7 treats these as consistent duplicates: the same fact at
different precision.

The suite has the mirror defect. A lookup case's expected fact is chosen by fact id, so the
NVIDIA FY2026 goodwill case expects the rounded 20.8B. Once the lookup answers 20,832M, that
case would grade as a confident wrong answer unless its ground truth moves to the precise
fact.

## Requirement

1. **Consistency rule.** `most_precise_consistent(facts)` takes `(value, decimals)` pairs.
   `decimals` is an int (XBRL `decimals`; it may be negative), or `None` for an exact value
   (`INF`, or no `decimals` attribute).
   - **Most precise:** `None` beats any int, and a larger int beats a smaller one.
   - **Result:** it returns the index of the first most precise fact when every fact is
     consistent with it, and otherwise `None`. An empty input also gives `None`.
   - **Consistent:** a fact with `decimals` *d* is consistent when
     |value − top| ≤ ½·10^(−d), the bound inclusive, so either rounding of a half is
     accepted. A fact with `decimals=None` is consistent only when it equals `top` exactly.
   - **Ties:** every fact tied at the top precision must have the same value as `top`,
     whether that precision is an int or `None`. Two facts at the same precision with
     different values are a conflict, not rounding, so the result is `None`. The winner
     therefore never depends on input order. (Re-lock, 2026-10-16: the first lock left a tie
     within half a unit order-dependent; `.claude/tdd/disputes/rounded-duplicates.md`.)
   - Compare exactly, on `decimal.Decimal(repr(value))`; never in binary float arithmetic.
2. **Lookup.** In `_build_result`, the facts of the winning concept at the latest period are
   collapsed when `most_precise_consistent` finds a winner among them:
   - the result's value, unit and citation are the winner's;
   - those facts count as **one** value, both for the AMBIGUOUS check and for the
     one-value confidence credit;
   - nothing else changes. Facts are not collapsed across concepts, periods or units.
   - Rows carry the precision under the key `decimals` (int, or `None` for exact). A row
     without the key is treated as exact, so existing callers behave as before. The lookup
     SQL must select `fact.decimals_value` (`NULL` when `is_decimals_infinity`). No view
     change is needed: join `fact` on `fact_id`.
3. **Suite ground truth (not locked).** When the suite builder picks a lookup case's
   expected fact, it substitutes the most precise consistent duplicate: same filing, concept,
   period, unit and dimensions.
   - Rebuild the suite and diff it. Only cases whose expected fact had a more precise
     duplicate may change; on 2026-10-16 that is NVIDIA FY2026 goodwill alone.

## Acceptance criteria

- **C1**: `most_precise_consistent` follows Requirement 1. Point cases:
  - `[(20.8e9, -8), (20.832e9, -6)]` gives 1;
  - `[(9.265e9, -6)] * 4 + [(9.3e9, -8)]` gives 0;
  - `[(20.9e9, -8), (20.832e9, -6)]` gives `None` (20.832e9 rounds to 20.8e9);
  - `[(20.85e9, -6), (20.8e9, -8)]` gives 0 (exactly half a unit: inclusive);
  - `[(20.851e9, -6), (20.8e9, -8)]` gives `None`;
  - `[(5.0, None), (5.0, -6)]` gives 0;
  - `[(5.0, None), (6.0, None)]` gives `None`;
  - `[(1.23, 2), (1.2, 1)]` gives 0 (positive decimals);
  - `[]` gives `None`;
  - `[(1e6, -7), (2e6, -7)]` gives `None` (a tie at the top precision with different
    values, although 1e6 is within half a unit of −7).

  Oracle: the rule, worked by hand.
- **C2**: Invariants.
  - Permuting the input moves the result to the permuted position of the same fact, or keeps
    it `None`.
  - Adding a fact whose value is `top` rounded to some coarser `d` (half-even) never changes
    which fact wins.
  - Adding a fact at that `d` whose value is more than ½·10^(−d) from `top` gives `None`.

  Oracle: the rule (metamorphic).
- **C3**: `_build_result` on the NVIDIA rows (rank-1 `Goodwill`, instant, no dimensions, no
  section hint), in either row order:
  - status OK, value 20,832,000,000, `citation.fact_id` 157;
  - confidence 0.8 (0.4 one value + 0.2 rank 1 + 0.2 no dimensions; threshold 0.70).

  The Amazon rows give OK with value 9,265,000,000. Inconsistent rows (20.9e9 at −8 with
  20.832e9 at −6) stay AMBIGUOUS. So do rows with different values and no `decimals` key.

  Oracle: `_score_confidence`'s fixed credits, by hand; the facts are from the corpus
  (2026-10-16).

## Interface

- `src.retriever.xbrl.xbrl_factstore_lookup.most_precise_consistent(facts:
  Sequence[tuple[float, int | None]]) -> int | None`

## Notes

- The source is XBRL 2.1 §4.6.7 (`decimals`) and the Inline XBRL duplicate-fact consistency
  rules. The fact ids and values come from the local corpus (public SEC filings), checked
  2026-10-16.
- **Out of scope:**
  - screens and `mv_derived_metrics`, which pick their inputs in SQL;
  - the 10 inconsistent groups, which stay AMBIGUOUS.
- **Validation after the merge:**
  - a slice of the working set and the affected lookups;
  - a ~60-case regression sample of stable lookups.

  No full run until the end (plan doc §0).
