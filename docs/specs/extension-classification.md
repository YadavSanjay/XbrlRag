# extension-classification — classify a filer extension by its calculation parent and statement position

Nuance N2 of [`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md) (area A, P0).
SEC's companyfacts API omits extension facts entirely, so a model using SEC's APIs cannot
see them, yet filers put real statement lines in their own namespace: McDonald's and P&G
tag "Income before provision for income taxes" with their own
`IncomeLossFromContinuingOperationsBeforeIncomeTaxes`, TSMC its non-operating total and
interest income, NVIDIA its marketable securities and capex lines. Today an extension
enters a concept family only when it is listed by hand under `filers:` in
`configs/concept_mapping/concept_family_coverage.yaml` (an `ext:` entry, expanded into the
filer's namespaces by `src/ingest/sec_edgar/ingest/layer0_populator.py`). There is no
general rule, so every new filer or new extension is invisible until someone lists it.

This spec adds that rule as a pure classifier. It uses only the filer's own filings: its
calculation linkbase (where the extension sits in a summation tree), its statements
(label and line position, from SEC's Financial Statement Data Sets `pre` table) and its
values (`num`). It classifies only when the evidence is exact, and otherwise leaves the
extension unclassified with a reason. It never guesses.

**Real data (read-only survey, 2026-11-07).** Across the 33 corpus filers (`xbrl_db`; no
held-out filer is in it) and the 149 filings that both `xbrl_db` and the filtered FSDS
quarters 2022q1–2026q3 hold (140 of them with an extension line), **345** distinct (filer, extension) pairs are non-abstract
IS/BS/CF lines with a consolidated value (979 statement lines), and **352** counting every
non-parenthetical line. Per filer (of the 352): TSM 85, VST 32, TSLA 28, ORCL 20, MCD 14,
NVDA 13, CEG 13, AMD 11, META 11, AMZN 10, INTC 10, MRK 9, AVGO 8, KO 8, MU 7, NFLX 7,
ABT 6, CSCO 6, GOOGL 6, QCOM 6, CRM 5, LMT 5, TXN 5, UPS 5, MSFT 4, UNP 4, COST 3, PG 3,
WMT 3, ADBE 2, LLY 2, AAPL 1, HD 0. Most are parts of a total: by calculation parent,
CashFromInvesting 65, CashFromOperations 52, CashFromFinancing 50, CurrentLiabilities 17,
Revenue 12, CurrentAssets 10, TotalLiabilities 10, CostOfRevenue 9, TotalAssets 8,
OperatingIncome 7, OperatingExpenses 7, PretaxIncome 6, NetIncome 4, others ≤ 3. The rule
below classifies **11** as a family's own measure (or stated substitute): PretaxIncome 3
(MCD, PG, ORCL), OtherNonOperatingIncome 2 and InterestIncome 2 (TSM), ShortTermInvestments
1 and Capex 1 (NVDA), StockRepurchases 1 (TSM), DepreciationAndAmortization 1 (LMT,
substitute); it leaves 341
unclassified (339 with no evidence, 2 with a label but no equal value). All 8 of the 11
that the hand list also covers agree with it; the 3 others are new (TSMC
`InvestmentsIncomeInterest` and `PaymentForAcquireOfTreasuryShares`, Lockheed Martin's D&A
line). The other 13 hand-listed extensions stay unclassified, and the hand list keeps
deciding them.

## Requirement

### R1. Subjects, concepts and scoping

`classify_extensions(filings, members, identities=None)` returns one result per
**subject**: a (`cik`, local name) of an extension that is presented on a statement line
with `stmt` in `IS`, `BS`, `CF` in at least one input filing of that `cik`.

- **Concept forms.** A concept is `<namespace>:<LocalName>` (the fact store's form, e.g.
  `http://fasb.org/us-gaap/2024:NetIncomeLoss`, `http://www.mcdonalds.com/20241231:X`) or a
  prefix form `us-gaap:X`, `srt:X`, `dei:X`, `ifrs-full:X`. The local name is the part after
  the last `:`.
- **Standard or extension.** A concept is *standard* when it is in a prefix form above, or
  its namespace starts with `http://fasb.org/`, `http://xbrl.sec.gov/`,
  `http://www.xbrl.org/`, `http://xbrl.ifrs.org/` or `https://xbrl.ifrs.org/`. A standard
  concept's short form is its prefix form: `http://fasb.org/us-gaap/<v>` is `us-gaap`,
  `http://fasb.org/srt/<v>` is `srt`, `http://xbrl.sec.gov/dei/<v>` is `dei`, and an IFRS
  taxonomy namespace ending in `/ifrs-full` is `ifrs-full`; the version or date never
  matters. Every other concept is an *extension*.
- **Identity.** An extension is identified by (`cik`, local name) only. Its namespace URI
  never matters (filers change it every year), and an extension whose local name equals a
  standard element's (TSMC's `InvestmentIncomeInterest`) is never that standard element.
- **Scoping.** A subject's result depends only on the input filings with its `cik`. An
  extension of one filer never matches, resolves or classifies another filer's
  same-named extension, and adding, removing or changing another filer's filings never
  changes it.

### R2. Inputs

- A **filing** is a mapping with `accession_no`, `cik` (10-digit, zero-padded), and:
  - `lines`: statement lines, each `{stmt, report, line, concept, plabel}` (FSDS `pre`:
    `report` and `line` are ints; parenthetical lines are not passed);
  - `arcs`: calculation arcs, each `{link_role, parent, child, weight}` (`weight` a
    `Decimal`; summation-item arcs of either arcrole, already filtered by the caller). A
    **tree** is one (`link_role`, `parent`); a (`link_role`, `parent`, `child`) repeated
    counts once;
  - `values`: consolidated values (no dimensions, no co-registrant), each
    `{concept, period, unit, value}`; `period` is any hashable key (two values are at the
    same period when their keys are equal), `value` a `Decimal` or None (nil; ignored).
- **members**: the family memberships of standard elements, each
  `{family, qname, role, substitution_code, cik}`: `qname` in prefix form, `role` one of
  `primary`, `substitute`, `component`, `cik` None for a generic row or a 10-digit CIK
  for a filer-scoped row. For a filer, its scoped row of a (`family`, `qname`) replaces
  the generic row of the same pair; scoped rows of other filers are ignored. A row whose
  `qname` is not a standard concept (an `ext:` row) raises `ValueError`: the hand list
  is not an input of the classifier (R6 compares against it).
- **identities**: as R3; None means `default_identities()`.

For a filer, **P(c)** is the set of families of which the standard concept *c* is a
`primary` member; an extension's P is always empty.

### R3. Statement identities (`default_identities`)

An identity has `id`, `statement`, `total` (a family) and `terms`, each
`{family, weight, required}` (`weight` a `Decimal`). `default_identities()` returns
exactly, in this order:

| id | statement | total | terms (family, weight, required) |
|---|---|---|---|
| `net_income` | IS | NetIncome | (PretaxIncome, 1, yes), (TaxExpense, −1, yes) |
| `pretax_income` | IS | PretaxIncome | (OperatingIncome, 1, yes), (OtherNonOperatingIncome, 1, yes), (InterestIncome, 1, no), (InterestExpense, −1, no) |
| `gross_profit` | IS | GrossProfit | (Revenue, 1, yes), (CostOfRevenue, −1, yes) |
| `operating_income` | IS | OperatingIncome | (GrossProfit, 1, yes), (OperatingExpenses, −1, yes) |

A **match** of a set of (concept, weight) pairs against an identity assigns each pair to a
distinct term whose family is in P(concept) and whose weight equals the arc weight; a
pair that fits no term, or more than one, makes the match fail.

### R4. Evidence

Evidence for a subject E of filer *k* names a (`family`, `role`, `substitution_code`) and
the accessions it comes from. Three rules give it:

1. **Identity, E is a term.** In a filing of *k* where E is presented on the identity's
   statement, a tree whose parent *T* is standard with the identity's total in P(T),
   whose children are E and other concepts that are all standard: if the others match the
   identity and leave exactly one required term unassigned, every optional term may stay
   unassigned, and that term's weight equals E's arc weight, the evidence is (term family,
   `primary`, None) from that filing. **Position:** when *T* is presented in the same
   `report` as E, E's `line` must be lower than *T*'s, else the tree gives no evidence.
   E can never fill an optional term (Intel's "Gains (losses) on equity investments" sits
   where only `InterestIncome` is open and gives nothing).
2. **Identity, E is the total.** In a filing of *k* where E is presented on the identity's
   statement, a tree whose parent is E and whose children are all standard: if they match
   the identity with every required term assigned, the evidence is (identity total,
   `primary`, None) from that filing. **Position:** every child presented in the same
   `report` as E has a lower `line` than E, else no evidence.
3. **Label and value.** E is presented in filing *A* of *k* on statement *X* with label
   *L*; a standard concept *S* is presented in filing *B* of *k* (*B* may be *A*) on the
   same statement *X* with the same normalized label (casefolded, keeping only `a`–`z`
   and `0`–`9`); and some value of E and some value of *S*, in any filings of *k*, have the
   same `period`, the same `unit` and the same non-zero value (exact `Decimal` equality).
   Then each member row of *S* for *k* with role `primary` or `substitute` gives
   evidence (its family, its role, its `substitution_code`) from *A* and every such *B*
   (every filing of *k* presenting *S* on *X* with that label). When the
   labels match but no such value pair exists, E is marked *label only*.

Two extensions in one tree are never resolved against each other: a tree with more than
one extension (E included) gives no identity evidence.

### R5. Guards and result

- **component_of(E)**: the union of P(*T*) over every tree of *k* whose standard parent
  *T* has E as a child. E is a part of those totals and never their measure.
- **contains(E)**: the union of P(*c*) over every standard child *c* of every tree whose
  parent is E. E is broader than those members and never their measure.
- Let *C* be the set of (`family`, `role`, `substitution_code`) named by E's evidence.
  - If some family in *C* is in component_of(E) ∪ contains(E): unclassified, reason
    `conflict` (never answer a total with a component, or a component with a total).
  - Else if *C* has more than one element: unclassified, reason `ambiguous`.
  - Else if *C* has one element: classified as it.
  - Else if E is label only: unclassified, reason `label_only`.
  - Else: unclassified, reason `no_evidence`.

A result has exactly the keys: `cik`, `local_name`, `family`, `role`
(`"primary"`, `"substitute"`, or None), `substitution_code` (str or None), `rules`
(sorted tuple of the rules that gave the classification's evidence: `"identity_term"`,
`"identity_total"`, `"label_value"`; empty when unclassified), `evidence` (sorted tuple
of accessions of that evidence; empty when unclassified), `component_of`, `contains`
(sorted tuples of families), `reason` (None when classified, else one of the four codes)
and `accessions` (sorted tuple of the filings presenting E on IS/BS/CF). An unclassified
result has `family`, `role` and `substitution_code` None. Results are sorted by (`cik`,
`local_name`), and nothing depends on the order of filings, lines, arcs, values or
members in the input.

### R6. Mapping rows (`extension_mapping_rows`)

`extension_mapping_rows(results, listed)` turns results into cik-scoped mapping rows
next to the hand list. `listed` holds the hand-listed rows `{cik, family, qname, role}`
with `qname` `ext:<LocalName>` (the `filers:` entries of
`concept_family_coverage.yaml` whose concept is an extension). It returns a dict with
exactly:

- `rows`: for every **classified** result whose (`cik`, local name) has no listed row,
  `{cik, family, qname: "ext:<LocalName>", role, substitution_code}`, sorted by (`cik`,
  `qname`, `family`). An unclassified result never yields a row, and a listed extension
  never yields one: the hand list wins.
- `agreements`: sorted tuple of (`cik`, local name) of classified results whose (`family`,
  `role`) equals a listed row of the same extension.
- `disagreements`: sorted tuple of (`cik`, local name) of classified results with listed
  rows none of which has their (`family`, `role`).
- `unclassified`: sorted tuple of (`cik`, local name, `reason`) of every unclassified
  result.

### R7. Wiring (not locked; implementer)

The reload builds the classifier's input from the filer's raw calculation tables (the
arcs `mv_calculation_network` holds, read with its own SELECT, not from the MV) and the
cached FSDS `pre` (IS/BS/CF lines, `inpth` 0) and `num` (no `segments`, no `coreg`) of
every ingested filing those quarters hold, never from `mv_financial_facts`, so the step
has no MV cycle and runs once, between `layer0-populate` and `mv-build` (amended
2026-11-11, maintainer decision; a filing not yet in a cached FSDS quarter stays
unclassified until it is). Members come from the committed mapping
(`committed_mapping_rows()`, standard rows only, without the dimension-sliced
`ProductRevenue`/`ServiceRevenue`). The reload appends `rows` after the hand-listed
`filers:` rows with source `extension_classifier`. A primary ranks after every
non-component rank the filer holds for the family (its IFRS rows included) and after the
highest rank the facts-count pass can give a primary, so it never outranks a standard or
hand-listed member and never ties with a later facts-count run (amended 2026-11-11). A
substitute ranks after the filer's last component-band row, as
`concept_family_coverage.yaml` ranks a listed substitute. `disagreements` and
`unclassified` are written to the reload's report; a disagreement never changes the
mapping. Update `src/ingest/sec_edgar/CLAUDE.md` and the coverage-file header.

## Acceptance criteria

Point oracles are the fixture filings (`test/spec/extension-classification/fixtures/`):
each filing's rendered statement and calculation linkbase, checked by hand, and the
maintainer's decisions (D-numbers of `docs/design/XbrlRag_Concept_Decisions.md`) where one
exists. All comparisons are exact (strings, tuples, `Decimal`s): no tolerance applies.

- **C1**: Subjects and identity. The fixtures' subjects are exactly the (cik, local name)
  of their FSDS `pre` rows whose `version` equals the filing's `adsh` (FSDS readme: a
  custom tag), 156 in all (MCD 14, PG 3, ORCL 14, TSLA 15, TSM 82, NVDA 6, LMT 4, AMD 4,
  INTC 5, LLY 2, AVGO 7), with no standard concept among them; TSMC's
  `InvestmentIncomeInterest` is a subject (it is not `us-gaap:InvestmentIncomeInterest`).
  `accessions` lists every filing presenting it. A members row with an `ext:` qname
  raises `ValueError`. Oracle: the FSDS readme rule, applied to the fixture rows in the
  test. Properties: consistently renaming an extension's namespace URI in every input of a
  filer, or its local name, changes nothing but that local name; permuting filings,
  lines, arcs, values and members changes nothing.
- **C2**: Identity rule. McDonald's (`0000063908-25-000012`), P&G (`0000080424-23-000073`)
  and Oracle (`0001193125-26-277521`) pre-tax extensions are `PretaxIncome` primaries;
  McDonald's and P&G by both identity rules, Oracle by `identity_term` only. TSMC's
  `NonOperatingIncomeExpense1` (20-F FY2023 and FY2024) and `NonOperatingIncomeExpenseNet`
  (FY2025) are `OtherNonOperatingIncome` primaries by `identity_term`. Oracle: the income
  statements by hand (MCD FY2023: 11,647 − 1,361 + 236 = 10,522 and 10,522 − 2,053 =
  8,469; PG FY2023: 18,134 − 756 + 307 + 668 = 18,353 and 18,353 − 3,615 = 14,738; ORCL
  FY2026: 20,606 − 4,599 + 3,547 = 19,554 and 19,554 − 2,467 = 17,087, in millions; TSMC
  FY2025: 1,936,091.7 + 105,563.0 = 2,041,654.7 million TWD), their calculation
  linkbases, and decisions D27 and D14. Properties: dropping the tax arc, adding an
  unmatched standard child or a second extension child, flipping E's weight, or placing
  the total above E in its report removes that tree's evidence.
- **C3**: Optional terms are never filled. Intel's `GainsLossesOnEquityInvestments`
  (`0000050863-25-000009`; −11,678 + 242 + 226 = −11,210) is unclassified, reason
  `no_evidence`, with `component_of` `("PretaxIncome",)`. Oracle: the statement (gains on
  equity investments are not interest income) and the rule. Property: in any tree where
  the required terms are filled by standard children, an extension child gets no
  identity evidence, whatever optional terms remain.
- **C4**: Label and value. NVIDIA's `MarketableSecuritiesAndEquitySecuritiesFVNI`
  (FY2026, "Marketable securities", 34,621 at 2025-01-31 equal to FY2025's
  `MarketableSecuritiesCurrent`) is a `ShortTermInvestments` primary (D39), and
  `PurchasesOfPropertyAndEquipmentAndIntangibleAssets` (FY2023; 1,833 for FY2023 equal to
  FY2025's `PaymentsToAcquireProductiveAssets`) a `Capex` primary (D41). TSMC's
  `InvestmentIncomeInterest` (FY2023) and `InvestmentsIncomeInterest` (FY2024) are
  `InterestIncome` primaries ("Interest Income", equal to `RevenueFromInterest`: 60,293.9
  and 87,213.4 million TWD; decision C8 of concept-family-coverage), and
  `PaymentForAcquireOfTreasuryShares` (FY2024) a `StockRepurchases` primary (3,089.2
  million TWD for FY2024 equal to FY2025's `PaymentsToAcquireOrRedeemEntitysShares`).
  Lockheed Martin's `DepreciationAndAmortizationIncludingDiscontinuedOperations` (FY2024)
  is a `DepreciationAndAmortization` **substitute** with code `includes_accretion`,
  inherited from FY2025's `DepreciationAmortizationAndAccretionNet` (1,559 and 1,430,
  same label). AMD's `PaymentsToAcquirePropertyPlantAndEquipmentFromDiscontinuedOperation`
  (10-Q Q2 2026: 22 against 494 and 1,197) and Intel's
  `PaymentsToAcquirePropertyPlantAndEquipmentFinancingActivities` are `label_only`.
  Oracle: the statements by hand. Properties: a pair equal only at zero, or at a
  different unit or period, gives no evidence; scaling every value of a filer by the same
  non-zero factor changes nothing.
- **C5**: Totals versus components. Broadcom's `CostofProductsSold` has `component_of`
  `("CostOfRevenue",)` (a component never answers its total); McDonald's `TotalOtherAssets` has `contains` `("Goodwill",)`
  and `component_of` `("TotalAssets",)`; Lilly's `CostOfSalesOperatingExpensesAndOtherNet`
  has `contains` `("CostOfRevenue", "OtherNonOperatingIncome", "ResearchAndDevelopment",
  "SellingGeneralAndAdministrative")` and `component_of` `("PretaxIncome",)` (D42: not
  CostOfRevenue; its restructuring child is only a substitute, so not listed); Oracle's
  `RestructuringAndOtherExpenses` has `contains` `("RestructuringCharges",)` and an empty
  `component_of` (its parent `CostsAndExpenses` is no family's primary); all four
  unclassified, reason `no_evidence`. Evidence for a family in `component_of`
  or `contains` gives `conflict`. Oracle: the calculation linkbases by hand. Property: no
  result's family is in its own `component_of` or `contains`; adding an arc that makes a
  classified extension a child of its own family's primary turns it into `conflict`.
- **C6**: Ambiguity. Evidence for two (family, role, code) gives `ambiguous`, never a
  pick. Oracle: the rule (real case: NVIDIA's marketable securities with a second
  label partner in another family). Property: adding evidence for a second family to any
  classified subject makes it `ambiguous`; removing all evidence makes it `no_evidence`
  or `label_only`.
- **C7**: Same name, two filers. McDonald's and P&G's
  `IncomeLossFromContinuingOperationsBeforeIncomeTaxes` are each classified from their own
  filing only (`evidence` their own accession); Oracle's and Tesla's
  `RestructuringAndOtherExpenses` differ (Oracle `contains` RestructuringCharges, Tesla
  `component_of` `("OperatingExpenses",)`), both unclassified. Oracle: the two filers'
  statements and linkbases. Properties: adding any filing of another filer, or removing
  every filing of one, changes no other filer's results; giving one filer's extensions
  another filer's namespace URI changes nothing.
- **C8**: Mapping rows. With the hand-listed `ext:` rows of the fixture filers, `rows` is
  exactly TSMC `InvestmentsIncomeInterest` (InterestIncome, primary),
  `PaymentForAcquireOfTreasuryShares` (StockRepurchases, primary) and Lockheed Martin's
  D&A line (DepreciationAndAmortization, substitute, `includes_accretion`); `agreements`
  are the eight listed-and-classified extensions; `disagreements` is empty; every
  unclassified result appears in `unclassified` with its reason. Oracle: the hand list
  (`concept_family_coverage.yaml`) and C2/C4. Properties: no unclassified or listed
  extension ever yields a row; adding listed rows only removes rows.
- **C9**: Identities. `default_identities()` returns R3's table exactly. Oracle: the
  table (the US GAAP and IFRS statement sums: net income = pre-tax income − tax; pre-tax
  income = operating income + non-operating items; gross profit = revenue − cost of
  revenue; operating income = gross profit − operating expenses). Property: passing
  `identities=()` leaves only `label_value` evidence.

## Interface

- `src.ingest.sec_edgar.xbrl.extension_classifier.default_identities() -> tuple[dict[str, Any], ...]`
  — each `{"id": str, "statement": str, "total": str, "terms": tuple[dict, ...]}`, a term
  `{"family": str, "weight": Decimal, "required": bool}`.
- `src.ingest.sec_edgar.xbrl.extension_classifier.classify_extensions(filings: Iterable[Mapping[str, Any]], members: Iterable[Mapping[str, Any]], identities: Iterable[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]`
- `src.ingest.sec_edgar.xbrl.extension_classifier.extension_mapping_rows(results: Iterable[Mapping[str, Any]], listed: Iterable[Mapping[str, Any]]) -> dict[str, Any]`

## Notes

- **Fixtures** (`test/spec/extension-classification/fixtures/`), 16 filings of 10 filers:

  | Filer | Accession | Form | Why |
  |---|---|---|---|
  | MCD | 0000063908-25-000012 | 10-K FY2024 | pre-tax extension, both identity rules; revenue parts; a total containing Goodwill |
  | PG | 0000080424-23-000073 | 10-K FY2023 | the same-named pre-tax extension at a second filer |
  | ORCL | 0001193125-26-277521 | 10-K FY2026 | pre-tax (term only); non-operating total under an extension parent; services revenue; restructuring total |
  | TSLA | 0001628280-25-003063 | 10-K FY2024 | the same-named restructuring extension as a component |
  | TSM | 0001193125-24-099840 | 20-F FY2023 | `InvestmentIncomeInterest` (a standard element's local name); non-operating total |
  | TSM | 0001193125-25-083423 | 20-F FY2024 | `InvestmentsIncomeInterest`, treasury shares, non-operating total |
  | TSM | 0001628280-26-025362 | 20-F FY2025 | the standard elements that replace them (IFRS) |
  | NVDA | 0001045810-23-000017 | 10-K FY2023 | capex extension, later replaced by a standard element |
  | NVDA | 0001045810-25-000023 | 10-K FY2025 | the standard capex and marketable-securities lines |
  | NVDA | 0001045810-26-000021 | 10-K FY2026 | marketable securities: an extension replacing a standard element |
  | LMT | 0000936468-25-000009 | 10-K FY2024 | D&A extension |
  | LMT | 0001628280-26-004195 | 10-K FY2025 | the standard substitute that replaces it |
  | AMD | 0000002488-26-000123 | 10-Q Q2 2026 | discontinued-operations capex: same label, different value |
  | INTC | 0000050863-25-000009 | 10-K FY2024 | an extension in an optional slot; capex label under financing |
  | LLY | 0000059478-26-000013 | 10-K FY2025 | a cost total containing cost of revenue (D42) |
  | AVGO | 0001730168-25-000121 | 10-K FY2025 | a cost-of-revenue component |

  - `fsds/<quarter>/{sub,pre,num,tag}.tsv`: SEC's rows byte-for-byte (via
    `data/cache/sec_fsds/filtered/`): every IS/BS/CF `pre` row of the filing with
    `inpth` 0; consolidated `num` rows of its extension lines and of the filer's
    standard lines whose label equals an extension line's label on the same statement;
    `tag` rows of its extension tags. `provenance.json` names each accession, quarter,
    zip URL, zip SHA-256 and size, and retrieval time.
  - `calc/<accession>.json`: every summation-item arc of every tree whose parent or a
    child is a presented extension, parsed from the filing's own calculation linkbase
    (`*_cal.xml`; Oracle's is embedded in its `.xsd`), with its URL, SHA-256 and the
    extension namespace (the schema's `targetNamespace`). Each filing's full arc set
    equals `mv_calculation_network` by local name (checked when the fixtures were built).
  - `members.json`: the committed mapping's standard rows for the fixtures' concepts
    (`committed_mapping_rows()` at `origin/main` d5efac8, 2026-11-07), without `ext:`
    rows and the sliced families; `listed.json`: the hand-listed `ext:` entries of the
    fixture filers from `concept_family_coverage.yaml`.
- **Limits, on purpose.** An extension never resolves another extension (Oracle's
  non-operating total sits under Oracle's own pre-tax extension, so it stays unclassified
  and the hand list (D14) keeps it). A label is matched only within the same filer and
  statement. Abstract lines never reach the classifier (FSDS `pre` holds line items only).
- **Found while surveying.** (1) `ext:` hand-listed rows are expanded into *every*
  namespace that has the local name, across filers: `concept_mapping` holds
  `http://www.pg.com/...:IncomeLossFromContinuingOperationsBeforeIncomeTaxes` scoped to
  McDonald's and the McDonald's namespaces scoped to P&G, Microsoft's
  `DepreciationAmortizationAndOther` under four `cisco.com` namespaces, and Tesla's
  `RestructuringAndOtherExpenses` under two `oracle.com` namespaces. The `cik_override`
  keeps them from matching today (Cisco's and Oracle's facts stay unmapped), but the rows
  are wrong and any join on `qname` alone would leak a family across filers. (2) The
  hand list has TSMC's `InvestmentIncomeInterest` (FY2022–23) but not the renamed
  `InvestmentsIncomeInterest` of FY2024. (3) `OtherNonOperatingIncome` holds both a total
  (`NonoperatingIncomeExpense`) and a part (`OtherNonoperatingIncomeExpense`) as primaries,
  so Oracle's non-operating total "contains" its own family; a guard based on the family
  cannot tell them apart (N3).
- **Out of scope.** Dimensional lines; notes-only extensions; IFRS filers' own
  identities beyond the four above; changing the hand list.
