# Dispute: concept-families-wave1, Costco's expected tagging (spec Notes)

**Status:** raised by the implementer on 2026-10-29. The spec Notes ask for a dispute when
the loaded facts contradict the expected Walmart/Costco tagging; they do for Costco. **No
locked test fails because of this.** The implementation is green and C4's Costco assertion
still holds. The open question is only whether the Costco filer entry, and the C4
assertion that requires it, should stay.

## Evidence (read-only SELECTs on `xbrl_db`, `mv_financial_facts`, 2026-10-29)

Walmart (CIK 0000104169) matches the spec. In every year FY2023–FY2025 it tags total
revenues as `us-gaap:Revenues` (611,289 / 648,125 / 680,985 $M) and net sales as
`RevenueFromContractWithCustomerExcludingAssessedTax` (605,881 / 642,637 / 674,538 $M),
both undimensioned. Today rank 1 resolves to net sales, so the filer entry is needed.

Costco (CIK 0000909832) does not match. In every canonical 10-K (FY2023, FY2024, FY2025)
and for every period, `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax`
carry the **same** undimensioned value, which is total revenue:

| period end | Revenues | RFCWC-ExclTax (undimensioned) |
|---|---|---|
| 2023-09-04 | 242,290 | 242,290 |
| 2024-09-02 | 254,453 | 254,453 |
| 2025-09-01 | 275,235 | 275,235 |

Net sales (249,625 $M, FY2024) appears only under `srt:ProductOrServiceAxis =
us-gaap:ProductMember`, and membership fees (4,828 $M) under `us-gaap:MembershipMember`.

## Consequence

- Today Costco's Revenue already resolves to total revenue (rank 1, the contract-revenue
  concept, carries 254,453). The Costco entry changes the rank-1 QName to `Revenues` but
  not the answered value in any loaded year.
- The entry is harmless and was kept, with a reason that states the observed tagging. It
  makes the total-revenue concept explicit for Costco, so a future filing that tags net
  sales undimensioned with the contract-revenue concept could not silently change the
  answer.

## Decision needed

1. **Keep** (implementer's recommendation): no re-lock is needed. Only the spec's narrative
   for Costco is inaccurate, and it can be corrected in a later docs change.
2. **Drop the Costco entry:** this needs a re-lock of C4
   (`test_committed_file_has_the_retail_overrides` asserts `(0000909832, Revenue)`) and a
   spec edit. After that the implementer removes the entry.
