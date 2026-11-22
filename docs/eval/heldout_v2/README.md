# Held-out filers for v2: the draw record

This record states how the eight held-out filers of the v2 evaluation (set H-F) were
chosen, and the result. The seed is the SHA-512 of the committed pool file, so nobody chose
it: the pool is built mechanically from committed lists before any draw. Anyone can
recompute the draw from the files here with one command.

Method: [`docs/specs/heldout-draw-v2.md`](../../specs/heldout-draw-v2.md); plan:
[v2 plan](../../design/XbrlRag_v2_Plan.md) §4.3–4.4; why the set exists:
[evaluation v2 architecture](../../design/XbrlRag_Eval_v2_Architecture.md) §5; results of the
G1/G2 look: [results_g1.md](results_g1.md).

## The pool

| File | SHA-256 |
|---|---|
| [`sp100_constituents.csv`](sp100_constituents.csv): the S&P 100 list (symbol, name, sector) | `04fc31a79811a141ebf48c9dcea3cddb9fbb31635ce485d8dc2f24a64a0fe127` |
| [`heldout_v2_pool.json`](heldout_v2_pool.json): the pool built from it | `61e9297a725188c4dac7c4469b575a516008f74950e6fc0dbed56463f405c1d7` |
| [`configs/eval/v2/heldout_exclusions.yaml`](../../../configs/eval/v2/heldout_exclusions.yaml): wave and left-out lists | `ba6d9892c49ba401c955aa166de4495c8608b304fbcabd013880a405a4a71a89` |
| SEC `company_tickers.json` as read on 2026-10-29 | `b5be63822ef78687908848b349820d9d6a962af9287e2164f44f4b217259f4ea` |

The constituents list is the one external test v1 used: the Wikipedia S&P 100 page,
revision 1378987012 (constituents as of 2026-09-21, citing iShares OEF), retrieved
2026-10-27T21:57Z.

Of 101 constituents, 63 are excluded, each for the first reason that applies:

| Reason | Count | Filers |
|---|---|---|
| Sector: Financials, Real Estate, Utilities | 19 | Banks, brokers, insurers in Financials, REITs, utilities (and V, a wave-2 filer, by sector first) |
| Already in the corpus | 13 | The development filers (GOOG with GOOGL, by CIK) |
| Wave 1 | 18 | CSCO, ADBE, CRM, QCOM, TXN, NFLX, KO, PG, WMT, COST, HD, MCD, LLY, MRK, ABT, UNP, LMT, UPS |
| Wave 2 | 7 | XOM, TMUS, JNJ, PEP, BA, CAT, DIS |
| Left out | 6 | UNH, CVS (insurers outside Financials), GE, GEV, MMM, SNDK (spin-offs with recast history) |

That leaves **38 candidates**:

- **Stratum S** (Information Technology, Communication Services), 13: ACN, AMAT, ANET,
  CMCSA, DELL, IBM, INTU, LRCX, NOW, PANW, PLTR, T, VZ.
- **Stratum D** (every other sector), 25: ABBV, AMGN, BKNG, BMY, COP, CVX, DE, DHR, EMR, FDX,
  GD, GILD, GM, ISRG, LIN, LOW, MDLZ, MDT, MO, PFE, PM, RTX, SBUX, TMO, UBER.

## The draw

- **Seed:** the SHA-512 of `heldout_v2_pool.json`'s bytes, upper-case hex:
  `C550B6DACDCAAD9F…` (in full in the draw file). A public beacon pulse would add only
  protection against someone choosing the seed; with the seed taken from the pool, there is
  nothing to choose.
- **Ranking:** each candidate's key is the SHA-256 of
  `"xbrlrag-heldout-v2|" + seed + "|" + CIK (10 digits)`; candidates are ranked by key within
  their stratum.
- **Selection:** walking each stratum in rank order, a candidate is eligible when it filed an
  inline-XBRL 10-K for each of FY2023, FY2024 and FY2025 (filing indexes only, no financial
  data read). The first **4** eligible are drawn and the next **2** are alternates.
- **Command** (run 2026-10-29; rerunning it gives the same bytes):

  ```powershell
  conda run -n py312 python scripts/eval/aprime_draw.py draw-v2 --pool docs/eval/heldout_v2/heldout_v2_pool.json --seed-from-pool --out docs/eval/heldout_v2/heldout_v2_draw.json
  ```

## The result

[`heldout_v2_draw.json`](heldout_v2_draw.json), SHA-256
`ddb3e44ee5c4c00310a6aee65fbcdff77ea7b18425dcf018d7339c5f7bd845fa`.

| Stratum | Drawn | Alternates |
|---|---|---|
| S | ANET, PANW, CMCSA, IBM | LRCX, AMAT |
| D | LOW, UBER, MDLZ, ISRG | TMO, BKNG |

Every candidate walked was eligible, so each stratum checked exactly six and has no
shortfall.

## After the draw

1. The draw result and its seed are committed here, with their hashes (done above).
2. The eight filers are ingested **only** into the quarantined `xbrl_heldout` database and
   the `qdrant-heldout` collections. They are never added to the development corpus, the
   lexicon or any dev suite.
3. Their questions are generated blind by the same suite builder, and only aggregates and
   failure-class counts leave a held-out run. The three permitted looks are logged with date,
   git sha and purpose.
4. An alternate replaces a drawn filer only if that filer's ingest fails the publish gate;
   the replacement and its reason are recorded here.
