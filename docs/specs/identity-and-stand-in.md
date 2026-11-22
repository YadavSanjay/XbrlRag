# identity-and-stand-in — cover-page identity that survives a re-run; no stand-in segment members

Two defects found while loading CEG, VST and INTC (2026-10-17). Both make XbrlRag state a wrong
thing with confidence, so they share one spec.

**A. `entity_master` identity.** `scripts/ddl/populate/entity_master.sql` pivots each filing's
DEI cover facts with no regard to their dimensions:

- *Co-registrants.* CEG's 10-K is a combined filing. Its cover tags `EntityRegistrantName`
  "CONSTELLATION ENERGY CORPORATION" (no dimension) and "CONSTELLATION ENERGY GENERATION, LLC" (on
  `dei:LegalEntityAxis`, member `ceg:ConstellationEnergyGenerationLLCMember`). `MAX()` picks the
  subsidiary, so CEG was named after it.
- *Share classes.* Alphabet tags `TradingSymbol` GOOGL on `us-gaap:CommonClassAMember` and GOOG on
  `goog:CapitalClassCMember`; its 2026 10-Q adds GOOGM and GOOGN (preferred depositary shares).
  `MIN()` picks GOOG, and the upsert overwrites the stored symbol. Re-running the populate for
  the corpus (any `bootstrap_db.py --phase data`, even with `--ticker`) turned Alphabet from GOOGL
  into GOOG: every GOOGL lookup and suite case broke until a manual `UPDATE`. The next full run
  undoes that fix.

**B. Stand-in members.** A one-dimension lookup keeps facts whose member label *contains* the
hint (docs/specs/segment-label-match.md). Intel's FY2025 10-K has no fact for its "Network and
Edge [Member]" segment (the filer reports that member in other years), but it has
"Client Computing Group, Datacenter And AI And Network And Edge [Member]" (Intel Products,
49,147M). A lookup for "network and edge" contains-matches that combined member and returns
49,147M as **OK, confidence 0.8**: a confident wrong number.

## Requirement

### A. `select_entity_identity(facts, existing)`

`facts` are one filing's DEI cover facts for one CIK, each
`{"name": <DEI local name>, "value": <str>, "dims": {<axis>: <member>}}` with prefixed QNames
(`dei:LegalEntityAxis`, `us-gaap:CommonClassAMember`). The populator maps namespaces to prefixes;
`us-gaap`, `srt`, `dei`, `exch` and `country` are **standard**, and any other prefix is the
filer's extension. `existing` is the stored row (`{"company_name", "trading_symbol"}`) or None.
It returns `{"company_name": str | None, "trading_symbol": str | None}`.

1. **Co-registrants are out.** A fact with a `dei:LegalEntityAxis` dimension describes another
   registrant in a combined filing and is ignored for every field.
2. **Company name** is the `EntityRegistrantName` fact with no dimensions (whitespace trimmed).
   If there is none, `company_name` is None (the stored name is kept).
3. **Trading symbol.** The candidates are the remaining `TradingSymbol` values, trimmed, without
   whitespace inside (`ORCL PRD` is a security, not the listing's ticker), de-duplicated.
   1. If `existing["trading_symbol"]` is a candidate, it is kept.
   2. Otherwise the symbol of the **primary listing** wins: one whose fact has no dimension
      other than an exchange (`dei:SecurityExchangeAxis`), or whose class member is standard,
      beats one on a filer-extension member.
   3. Ties are broken by the shortest symbol, then alphabetically.
   4. With no candidates, `trading_symbol` is None (the stored symbol is kept).
4. The result never depends on the order of `facts`.

### B. `refuse_stand_in(hint, matched_labels, catalog_labels)`

`hint` is a one-dimension lookup's member hint; `matched_labels` are the member labels of the
facts the lookup kept for it; `catalog_labels` are the filer's member labels on the axes the
lookup searched, any year (`dimension_catalog`). Keys are `member_label_key`
(docs/specs/segment-label-match.md).

5. Returns True when **no** matched label's key equals the hint's key, and **some** catalog
   label's key does. The member asked for exists, so a fact found only by containment is
   another member standing in for it (a combination, or a different segment).
6. Otherwise False: an exact match exists, or the filer has no member by that name (today's
   containment behaviour stands, e.g. a hint "license support" naming part of a longer label).
7. **Wiring (in the lookup).** When `refuse_stand_in` is True, the lookup returns
   `UNRESOLVED_DIMENSION`, with no value and no citation, and a note naming the member that has
   no fact and the labels set aside. It is never OK or AMBIGUOUS with a stand-in's value.

## Acceptance criteria

- **C1**: Co-registrant facts are ignored. On CEG's cover (Notes), `company_name` is
  "CONSTELLATION ENERGY CORPORATION" and `trading_symbol` "CEG". Exact. Property: adding any
  `dei:LegalEntityAxis` facts (any name, symbol or CIK) to a cover never changes the result.

  Oracle: Requirement 1–2; the CEG 10-K cover (accession 0001868275-26-000032).
- **C2**: The primary listing wins and a stored symbol survives. On Alphabet's 2026 10-Q cover
  (GOOGL, GOOG, GOOGM, GOOGN), `trading_symbol` is "GOOGL" with no stored row, "GOOGL" with
  stored "GOOGL", and "GOOG" with stored "GOOG" (still listed). With a stored "GOOGX" (no longer
  listed) it is "GOOGL". Oracle's cover (ORCL on `us-gaap:CommonStockMember`) gives "ORCL";
  Vistra's (VST on an exchange dimension only) gives "VST"; a cover whose only symbol is
  "ORCL PRD" gives None. Exact. Property: the result is a function of the set of facts (any
  permutation gives the same answer), and a stored symbol that is a candidate is always kept.

  Oracle: Requirement 3; the covers in Notes.
- **C3**: A combined member never stands in. For INTC FY2025, hint "network and edge", matched
  ["Client Computing Group, Datacenter And AI And Network And Edge [Member]"], catalog including
  "Network and Edge [Member]": True. With an exact match also present: False. With hint
  "license support", matched ["Cloud Services And License Support [Member]"] and no such member
  in the catalog: False. Exact. Property: True exactly when Requirement 5's condition holds,
  computed independently in the test; adding the hint's own label to `matched_labels` always
  makes it False.

  Oracle: Requirement 5–6; INTC's FY2025 catalog (Notes).

## Interface

- `src.ingest.sec_edgar.ingest.entity_identity.select_entity_identity(facts: list[dict],
  existing: dict | None) -> dict` (new module).
- `src.retriever.xbrl.xbrl_factstore_lookup.refuse_stand_in(hint: str, matched_labels:
  list[str], catalog_labels: list[str]) -> bool`.

## Notes

- **Covers** (public SEC filings, read 2026-10-17):
  - CEG 10-K FY2025: `EntityRegistrantName` "CONSTELLATION ENERGY CORPORATION" (no dims) and
    "CONSTELLATION ENERGY GENERATION, LLC" (`dei:LegalEntityAxis` =
    `ceg:ConstellationEnergyGenerationLLCMember`); `EntityCentralIndexKey` 0001868275 (no dims)
    and 0001168165 (same LLC dim); `TradingSymbol` "CEG" (no dims).
  - Alphabet 10-Q 2026 Q2: "Alphabet Inc."; `TradingSymbol` GOOGL (`dei:EntityListingsClassOfStockAxis`
    = `us-gaap:CommonClassAMember`), GOOG (= `goog:CapitalClassCMember`), GOOGM and GOOGN
    (= `goog:DepositaryShares…PreferredStockMember`).
  - Oracle 10-K FY2025: ORCL on `us-gaap:CommonStockMember`. Vistra 10-Q 2026 Q2: VST on
    `dei:SecurityExchangeAxis` = `exch:XNYS`.
  - INTC FY2025 `StatementBusinessSegmentsAxis` labels: Altera, Client Computing Group, Client
    Computing Group, Datacenter And AI And Network And Edge, Datacenter and AI, Intel Foundry,
    Mobileye, Network and Edge, Other Operating Segment (each with ` [Member]`).
- **Wiring (not locked; implementer's unit tests):** the `entity_master` populate takes
  `company_name` and `trading_symbol` from `select_entity_identity` (the SQL's `MAX()`/`MIN()`
  for those two fields goes, and the upsert no longer overwrites a kept symbol). `dims` are the
  context's **explicit** dimensions: Arelle's `context_dimension` also stores every axis's
  default (domain) member, so join with `cd.is_default = FALSE`, as the MVs do; without it every
  cover fact looks dimensioned. The lookup
  fetches the filer's catalog labels for the searched axes and applies `refuse_stand_in` after
  the exact-member filter.
- **Prototype check (2026-10-17, read-only on xbrl_db):** with no stored row, the rules give the
  current (corrected) ticker and name for all 15 filers, including GOOGL and CEG; none of the
  suite's 50 aq-3d segment-member cases is refused.
- **Validation after the merge:** re-run the `entity_master` populate for the whole corpus: GOOGL
  stays GOOGL and CEG keeps its own name, and every other row is unchanged. Live lookups: INTC
  FY2025 "network and edge" → UNRESOLVED_DIMENSION; "intel foundry" stays OK; a regression slice
  of the suite's segment cases (aq-3d, aq-8b) loses nothing.
