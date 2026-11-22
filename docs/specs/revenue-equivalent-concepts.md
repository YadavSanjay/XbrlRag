# revenue-equivalent-concepts — total revenue under either standard concept earns the rank credit

A lookup earns +0.2 confidence when its winning concept is the filer's preferred QName
(`concept_pref_rank == 1`), and nothing for a fallback. Usually that protects the answer. A
rank-2 concept is often a near-miss for the family: a current portion, a related measure.

It fails when a filer switches between two concepts that are the same measure. Alphabet tagged
total revenue as `RevenueFromContractWithCustomerExcludingAssessedTax` in FY2022–24 (so its
per-filer rank 1 is that concept), and as `Revenues` in FY2025.
- The FY2025 lookup resolves the right fact (402,836M) at rank 2 with confidence 0.6
  (0.4 one value + 0.2 no dimensions), below the 0.70 threshold, so it is refused.
- This is the only Revenue cell of its kind in the corpus (2026-10-16).

A general "best concept in the filing earns the credit" rule was considered and rejected. Of the
7 corpus cells whose best concept is a rank-2 primary, Alphabet FY2022–23 `DeferredRevenue`
would be answered with `ContractWithCustomerLiabilityCurrent`, the current portion only. So
the credit is extended only where two concepts are the same measure: the two standard
total-revenue concepts.

## Requirement

1. **`rank_credit(concept_family, concept, rank)`** is True when either:
   - `rank == 1`; or
   - the concept's local name (the part after the last `:`) is in
     `EQUIVALENT_PRIMARY_CONCEPTS[concept_family]`, and `rank` is not in the component band
     (50–99).

   Otherwise it is False, including for `rank` None. Only `Revenue` has an entry:
   `{"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"}`.
2. **The lookup** uses `rank_credit` for the +0.2 rank credit in `_score_confidence`.
   Nothing else changes: the other credits, the ambiguity checks and the ordering.

## Acceptance criteria

- **C1**: `rank_credit` point cases:
  - (`Revenue`, `…2025:Revenues`, 2) → True;
  - (`Revenue`, `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`, 3) → True;
  - (`Revenue`, `…:SalesRevenueNet`, 2) → False;
  - (`DeferredRevenue`, `…:ContractWithCustomerLiabilityCurrent`, 2) → False;
  - (`NetIncome`, `…:ProfitLoss`, 2) → False;
  - (`Revenue`, `…:Revenues`, None) → False;
  - (`Revenue`, `…:Revenues`, 55) → False;
  - (any family, any concept, 1) → True.

  Properties:
  - rank 1 always earns the credit;
  - a family with no entry never credits a rank other than 1;
  - the namespace prefix (a URI or `us-gaap:`) does not matter.

  Oracle: Requirement 1.
- **C2**: `_build_result` on corpus rows (2026-10-16):
  - Alphabet FY2025 Revenue: five `Revenues` facts (rank 2) for the current period plus prior
    comparatives, no dimensions. → OK, 402,836,000,000, confidence 0.8.
  - Alphabet FY2023 DeferredRevenue: `ContractWithCustomerLiabilityCurrent` (rank 2,
    4,137M) and `…Noncurrent` (rank 3, 911M). → stays AMBIGUOUS at confidence 0.6.
  - Metamorphic: renaming a rank-2 Revenue winner's concept between the two equivalents
    changes nothing in the result.

  Oracle: `_score_confidence`'s fixed credits by hand.

## Interface

- `src.retriever.xbrl.xbrl_factstore_lookup.rank_credit(concept_family: str, concept: str, rank: int | None) -> bool`

## Notes

- The facts are from the local corpus (public SEC filings), checked 2026-10-16. The suite
  case `aq-1a-revenue-lookup:fy=2025-ticker=GOOGL` expects fact 35948, one of the five
  duplicates.
- **Out of scope:** per-year concept overrides, and other families (the maintainer chose
  Revenue only, 2026-10-16).
- **Validation after the merge:**
  - the GOOGL FY2025 revenue case;
  - the revenue lookups and screens;
  - a regression sample.
