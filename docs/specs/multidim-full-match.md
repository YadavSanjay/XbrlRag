# multidim-full-match — credit a multi-dimension lookup whose hints identify the fact exactly

Phase 5 of the accuracy plan, the deterministic half. The LLM half is the prompt change on
`feat/multidim-single-need`: decomposer v2, router v2 and planner v3 keep "one metric for X
and Y" as one lookup carrying every qualifier.

## Requirement

A multi-dimension lookup earns the +0.2 "dimension matched" confidence credit only when every
member hint **equals** a member label after normalisation (`_dimension_match_exact`). The
planner writes hints the way an analyst does, not as full labels:

- Apple FY2024 "cash and cash equivalents at carrying value for Corporate Debt Securities and
  Fair Value, Inputs, Level 2" is planned with
  `{"instrument": "corporate debt securities", "fair_value_level": "level 2"}`.
- The lookup finds exactly one fact: 13665, 26,000,000, with the preferred concept and two
  dimensions.
- "level 2" is inside "Fair Value, Inputs, Level 2" but not equal to it, so confidence is
  0.6 < 0.70 and the step is AMBIGUOUS. The answer is refused although it is right.

With every qualifier written out as the full label, the lookup resolves 45 of the 50 `aq-8b`
cases. With analyst-style hints, almost none.

After this change, a lookup with **two or more** hints also earns the credit when the hints
pick out the fact's dimensions **one-to-one**: each hint is contained in a different member
label, and the fact has exactly as many dimensions as there are hints.

Requiring equal counts is the safety condition. A Phase 3 analysis found that crediting partial
matches turns a question about one cell into a confident answer about another. Two hints
against a three-dimension fact stay uncredited. A single-dimension lookup is unchanged.

## Acceptance criteria

- **C1**: `dimensions_fully_matched(dimension_labels, member_hints)` is `True` iff all of these
  hold:
  - there are at least 2 hints;
  - `dimension_labels` has exactly as many entries as there are hints;
  - a one-to-one assignment of hints to labels exists in which every normalised hint is a
    substring of its label's normalised form.

  Normalisation is `_norm_label`: lowercase, `[Member]` and brackets removed, whitespace
  collapsed. `None` or empty labels give `False`. Oracle: the definition. Point cases are
  Apple fact 13665 (true for "corporate debt securities" + "level 2") and Microsoft fact
  104489 (three dimensions: false for two hints).
- **C2**: `XbrlFactstoreLookup()._build_result(...)` for a two-hint lookup on rows from the
  multi-dimension path:
  - **One fact matched one-to-one** (Apple 13665, preferred concept, no section hint):
    status OK, value 26,000,000, confidence 0.8 (0.4 one value + 0.2 preferred + 0.2
    dimensions).
  - **The same hints against a three-dimension fact:** confidence 0.6, AMBIGUOUS.
  - **Two hints both contained only in the same label:** no credit, AMBIGUOUS.

  Oracle: the confidence formula in `_score_confidence`, by hand.
- **C3**: Metamorphic properties of `dimensions_fully_matched`:
  - Permuting the hints or the labels does not change the result.
  - Adding a label (a dimension the hints do not mention) makes a true result false.
  - Replacing a hint by its full label keeps a true result true.

  Oracle: the definition.

## Interface

New function (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_lookup.dimensions_fully_matched(dimension_labels: Mapping[str, str] | None, member_hints: Sequence[str]) -> bool`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `XbrlFactstoreLookup._dimension_match_exact` uses `dimensions_fully_matched` for a
  multi-dimension spec list, **replacing** the exact-equality rule. Containment with equal
  counts covers every legitimate exact match. An exact match against a fact with
  *additional* dimensions no longer earns the credit; today it does, which is the
  under-specification this spec excludes. The single-dimension (flat path) rule is unchanged.

## Notes

- Evidence (2026-10-14, local `xbrl_db`):
  - Apple fact 13665, `CashAndCashEquivalentsAtCarryingValue` FY2024: value 26,000,000,
    dimensions FinancialInstrumentAxis "Corporate Debt Securities [Member]" and
    FairValueByFairValueHierarchyLevelAxis "Fair Value, Inputs, Level 2 [Member]".
  - Microsoft fact 104489, FY2026: three dimensions — "US Government Agencies Debt
    Securities", "Debt Securities", "Fair Value, Inputs, Level 2".
- The distinct-values check is unchanged: a lookup matching several facts with different
  values stays AMBIGUOUS whatever the credit.
- Out of scope: the 4 Tesla debt cases that stay AMBIGUOUS because two debt concepts share
  preference rank 1 (Phase 3(a)).
