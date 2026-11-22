# entity-resolution-margin — a lookalike or unknown name never resolves to a real filer

Area I of the test strategy, nuance N10
([`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md)). Verified 2026-11-07:
`EntityExtractor._resolve_one_company` falls back to `pg_trgm` and accepts the best
`similarity(lower(company_name), lower(mention))` at **0.30**, with no runner-up check. Over
SEC's company ticker list that rule resolves **774 of 7,970** distinct names outside the
33-filer corpus (9.7%) to a corpus filer: "MICROCHIP TECHNOLOGY INC" to Micron (0.74),
"Riot Platforms, Inc." to Meta Platforms (0.58), "VISTEON CORP" to Vistra (0.56), "Core
Laboratories Inc. /DE/" to Abbott Laboratories (0.41), "CONSTELLATION BRANDS, INC." to
Constellation Energy (0.37). The pipeline then answers, with audited-looking citations, for a
company nobody asked about.

## Requirement

A company mention resolves to a filer only when

1. it is **exact**: a ticker, a committed alias, or the filer's name after the normalization
   below (an "exact legal-name match"); or
2. its **fuzzy** best match clears a threshold **and** beats the runner-up by a margin.

Otherwise the result is not resolved (`ambiguous` or `unresolved`) and names the closest
candidates, so the pipeline asks or refuses instead of answering for the wrong company.

### R1. Name key: `company_name_key(name)`

Applied to the mention and to every filer name, in this order:

1. `casefold()`; map `’` (U+2019) to `'`; delete every `'` ("McDonald's" → "mcdonalds").
2. Replace every `&` with ` and `.
3. Drop a trailing state-of-incorporation tag: a `/` followed by optional spaces, 1–3
   letters and an optional `/`, at the end of the name, trailing whitespace ignored
   (`INC/DE`, `CORP /NEW`, `/CO/`,
   `INC / MA`, `Ltd./ADR`).
4. Replace every run of characters other than ASCII `a`–`z` and `0`–`9` with one space.
5. Split into tokens and drop every **legal-form word**: `inc`, `incorporated`, `corp`,
   `corporation`, `co`, `company`, `ltd`, `limited`, `plc`, `llc`, `lp`, `the`, `sa`,
   `nv`, `ag`, `se`.
6. Drop leading and trailing `and` tokens (left by "Eli Lilly **and** Company",
   "Merck **&** Co.").
7. Join the remaining tokens with single spaces (possibly the empty string).

The key is idempotent.

### R2. Trigram similarity: `trigram_similarity(a, b)`

`pg_trgm`'s `similarity()` (PostgreSQL documentation, F.35 pg_trgm), restricted to ASCII:
lower-case the string; its words are the maximal runs of `a`–`z` and `0`–`9`; each word is
padded with two spaces in front and one behind, and contributes every 3-character
substring of the padded word; the string's trigram set is the union over its words.
`similarity = |A ∩ B| / |A ∪ B|`, and `0.0` when either set is empty. It is symmetric, in
`[0, 1]`, and 1.0 for two strings with the same trigram set.

### R3. Resolution: `resolve_company(mention, filers, aliases)`

`filers` is the universe: rows `(cik, ticker, company_name)` (ticker may be None; one CIK
may have several rows, e.g. one per share class). `aliases` is the normalized alias table
of `load_company_aliases` (docs/specs/company-aliases.md). Tiers, first hit wins:

1. **Ticker** (`exact_ticker`): `normalize_company_mention(mention)` equals
   `ticker.casefold()` for rows of exactly one CIK. Rows of two or more CIKs →
   `ambiguous` with those CIKs.
2. **Alias** (`alias`): `match_company_alias(mention, aliases)` gives a CIK. If the CIK has
   a row in `filers` it resolves; otherwise the result is `unresolved` with no candidates
   (an alias is authoritative and is never handed to fuzzy matching).
3. **Exact name** (`exact_name`): the mention's key is non-empty and equals
   `company_name_key(company_name)` for rows of exactly one CIK. Two or more CIKs →
   `ambiguous` with those CIKs.
4. **Fuzzy** (`fuzzy_name`): each CIK's score is the highest
   `trigram_similarity(key(mention), key(company_name))` over its rows. With `s1` the best
   score and `s2` the best score of any **other** CIK (0.0 if there is none), the best CIK
   resolves iff

   - `s1 ≥ FUZZY_THRESHOLD = 0.75`, and
   - `s1 − s2 ≥ FUZZY_MARGIN = 0.10`

   (both compared with a 1e-9 slack, so an exact rational 0.75 passes).
5. Otherwise the candidates are the CIKs scoring `≥ CANDIDATE_FLOOR = 0.30`, best first
   (ties by CIK ascending), at most 3. With at least one candidate the status is
   `ambiguous`; with none it is `unresolved`.

The result is a `CompanyMatch` with `status`, `cik`, `ticker`, `company_name`, `score`,
`candidates`:

| status | cik / ticker / company_name | score | candidates |
|---|---|---|---|
| `exact_ticker`, `alias`, `exact_name` | the filer's (the matched row for a ticker, else the CIK's first row in `filers` order) | 1.0 | `()` |
| `fuzzy_name` | the best CIK's (its best-scoring row) | `s1` | `()` |
| `ambiguous` | None | the best score (1.0 for a ticker or name collision) | CIKs, as above (collision: the colliding CIKs ascending) |
| `unresolved` | None | the best fuzzy score, or 0.0 | `()` |

The result never depends on the order of `filers` (except which row supplies
`ticker`/`company_name` for a CIK with several rows).

### R4. Committed aliases

Exact matching makes partial names that fuzzy matching used to reach (at 0.35–0.64) stop
resolving. The ones analysts use, and the second Alphabet share class, become committed
aliases in `configs/entities/company_aliases.yaml`: "Abbott" → ABT, "Amazon" → AMZN,
"Cisco" → CSCO, "Lockheed" → LMT, "Micron" → MU, "GOOG" → GOOGL's CIK. (Alphabet's
`entity_master` row keeps GOOGL, docs/specs/identity-and-stand-in.md.)

### Choice of the numbers (evidence, 2026-11-07)

Universe: the 33 corpus filers' `entity_master` names. Wrong side: every distinct name in
SEC's `company_tickers.json` whose CIK is not a corpus filer (7,960 keys). Right side:
one-character typos (delete, substitute, transpose) of SEC names. Scores are
`trigram_similarity` of keys (R1, R2).

- **Threshold.** The best wrong score is **0.696** ("microchip technology" → Micron,
  16/23); 20 wrong names score ≥ 0.50, 2 score ≥ 0.60, none ≥ 0.70. 0.75 leaves a 0.054
  buffer. Of 1,293 typos of corpus names, 199 clear 0.75 (all to the right filer); the
  rest become `ambiguous` and name the filer, which the pipeline asks about.
- **Margin.** Inside 33 filers the runner-up is never close (nearest pair: Microsoft /
  Micron Technology, 0.217). It matters when the universe grows: over a sample of 400
  typos scored against all ~10,000 SEC names, every typo whose best match was the wrong
  company had a margin ≤ 0.044 when its score was ≥ 0.50; 0.10 is more than twice that,
  and costs 1 of 138 right matches at the 0.75 threshold.
- **Candidate floor.** 0.30, the old acceptance threshold: anything the old rule would
  have accepted is now named as a candidate instead.

## Acceptance criteria

- **C1**: `company_name_key` gives the keys below for real filer spellings (entity_master
  and SEC), exactly: "McDONALD’S CORPORATION" → "mcdonalds"; "QUALCOMM INC/DE" →
  "qualcomm"; "COSTCO WHOLESALE CORP /NEW" → "costco wholesale"; "ELI LILLY AND COMPANY"
  and "ELI LILLY & Co" → "eli lilly"; "Merck & Co., Inc." → "merck"; "PROCTER & GAMBLE CO"
  → "procter and gamble"; "AMAZON.COM, INC." → "amazon com"; "HOME DEPOT, INC." and "The
  Home Depot" → "home depot"; "VERTEX PHARMACEUTICALS INC / MA" → "vertex
  pharmaceuticals"; "Taiwan Semiconductor Manufacturing Company Limited" → "taiwan
  semiconductor manufacturing"; "COCA-COLA EUROPACIFIC PARTNERS plc" → "coca cola
  europacific partners"; "JOHNSON & JOHNSON" → "johnson and johnson". Property: the key
  is idempotent and unchanged by case, whitespace runs, surrounding punctuation, a leading
  "The" and an appended legal suffix (", Inc.", " Corp.", " Co.", " Ltd", " plc").
  Oracle: R1 applied by hand.
- **C2**: `trigram_similarity` equals PostgreSQL `pg_trgm` `similarity()` (read on xbrl_db,
  2026-11-07) and the hand count: ("micron technology", "microchip technology") = 16/23;
  ("texas instrument", "texas instruments") = 16/19; ("constellation", "constellation
  energy") = 14/21; ("abcdefghijkl", "abcdefghijklm") = 12/15; ("abcdefghijkl",
  "abcdefghijklmn") = 12/16; ("abcdefghijkl", "abcdefghijxy") = 10/16; ("abcdefgh",
  "abcdefghx") = 8/11; to 1e-9. Property: symmetric, in [0, 1], 1.0 for a string with
  itself, case-insensitive and word-order-insensitive. Oracle: R2 / pg_trgm.
- **C3**: Exact tiers. With the corpus universe and the committed alias table, tickers
  ("AAPL", "googl", "Meta", "TSM"), aliases ("TSMC", "Google", "Facebook", "P&G", "Coke",
  "TI", "Lilly", "Costco", and R4's "Abbott", "Amazon", "Cisco", "Lockheed", "Micron",
  "GOOG") and names ("Apple", "Alphabet", "The Home Depot", "Procter and Gamble",
  "Coca-Cola", "McDonald's", "Eli Lilly", "Merck & Co", "Union Pacific", "Texas
  Instruments", "Constellation Energy", "Advanced Micro Devices", "Micron Technology", …)
  resolve to SEC's CIK for that company with the expected status, score 1.0 and no
  candidates; so does every SEC title of a corpus filer. Two CIKs sharing a ticker or a
  key → `ambiguous` with both. An alias whose CIK is not in the universe → `unresolved`.
  Properties: an exact match is unchanged by case, whitespace and surrounding punctuation
  of the mention, by the order of `filers`, and by adding any unrelated company (new CIK,
  ticker and key) to the universe. Oracle: SEC company_tickers.json (name → CIK).
- **C4**: Threshold and margin. Corpus universe: "micrn technology" → Micron, `fuzzy_name`,
  score 15/20; "Texas Instrument" → TXN, `fuzzy_name`, 16/19; "Constellation" →
  `ambiguous`, candidates (CEG), score 14/21. Adding Microchip (SEC row) to the universe
  turns "micrn technology" `ambiguous` with candidates (MU, MCHP) (15/20 − 15/23 < 0.10).
  Synthetic: a lone filer at 12/16 = 0.75 resolves; at 8/11 it does not; 12/15 vs a
  runner-up at 12/16 (margin 0.05) → `ambiguous`; 12/15 vs 10/16 (margin 0.175) →
  resolves; nothing ≥ 0.30 → `unresolved`, no candidates. Properties: a mention
  equidistant to two filers never resolves (both are candidates); the result is
  independent of the order of `filers`. Oracle: R3 with C2's hand counts.
- **C5**: Lookalikes and non-corpus companies. With the corpus universe and committed
  aliases, no SEC company outside the corpus resolves to a corpus filer — by its title
  (every `named` and `near` row of the fixture: Microchip Technology, Constellation
  Brands, Texas Pacific Land, Coca-Cola Europacific Partners, Coca-Cola Consolidated, Coca
  Cola FEMSA, Applied Materials, AbbVie, Johnson & Johnson, Union Bankshares, Union
  Electric, Alpha Metallurgical Resources, Meta Critical Minerals, Apple Hospitality REIT,
  Riot Platforms, the STRATS trusts for P&G and Wal-Mart, S&P 100 constituents, and every
  other SEC name scoring ≥ 0.30 against a corpus filer) or by its ticker; nor does "J&J".
  "Microchip Technology" and "Constellation Brands" are `ambiguous` and name Micron and
  Constellation Energy. Metamorphic: with the whole excerpt as universe, every row's
  title resolves to that row's CIK (or is `ambiguous` among CIKs sharing its key), and
  case/whitespace/punctuation changes to a non-corpus title never make it resolve to a
  corpus filer. Oracle: SEC company_tickers.json.

## Interface

New module `src/retriever/xbrl/entity_resolution.py`:

- `company_name_key(name: str) -> str`
- `trigram_similarity(a: str, b: str) -> float`
- `resolve_company(mention: str, filers: Sequence[tuple[str, str | None, str]], aliases: Mapping[str, str]) -> CompanyMatch`
- `class CompanyMatch(NamedTuple)`, fields in this order: `status: str`, `cik: str | None`,
  `ticker: str | None`, `company_name: str | None`, `score: float`,
  `candidates: tuple[str, ...]` (CIKs).
- Constants `FUZZY_THRESHOLD = 0.75`, `FUZZY_MARGIN = 0.10`, `CANDIDATE_FLOOR = 0.30`,
  `MAX_CANDIDATES = 3`.

## Notes

- **Fixtures** (`test/spec/entity-resolution-margin/fixtures/`):
  `sec_company_tickers_excerpt.json` — rows of https://www.sec.gov/files/company_tickers.json
  retrieved 2026-11-07 (full file SHA-256
  `76b4b2abb9f7dd0e9cff38e2f20336952bb41806057b2dd1033e244f3e1faec2`, 796,507 bytes); the
  rows of the corpus CIKs (`why=corpus`), named lookalikes and S&P 100 names (`named`), and
  every other row scoring ≥ 0.30 against a corpus filer either under today's rule (raw
  lower-cased `pg_trgm`, so all 774 names it mis-resolves are covered) or on R1 keys
  (`near`); 1,169 rows. Selection only; each row's CIK is the oracle.
  `corpus_filers.json` — `entity_master` (cik, trading_symbol, company_name), read
  2026-11-07: the names exactly as the resolver sees them at run time.
- **Wiring (implementer).** `EntityExtractor._resolve_one_company` calls `resolve_company`
  over the `entity_master` rows (cik, trading_symbol, company_name) and the loaded alias
  table, and maps the result onto `ResolvedCompany` (`resolution` = the status, with the
  new value `exact_name`; `similarity` = score). An `ambiguous` result's candidates are
  surfaced (log and the router's resolution notes, naming each candidate's ticker and name)
  so the answer can ask "did you mean …". `COMPANY_SIMILARITY_THRESHOLD` and the
  `pg_trgm` acceptance go. Graduated spec tests fake the database by SQL shape: the ticker
  query (`upper(trading_symbol)`), the CIK query (`WHERE cik = %s`) and a `similarity(`
  query answered by `fetchone()`, and some fakes have no `fetchall()`; their mentions are
  tickers or names that must stay unresolved ("Nobody Corp"). Keep them passing (run
  `pytest test/spec`). Unit tests of the old SQL order (`test_entity_extractor_aliases.py`)
  are the implementer's to update.
- **Known edge, not tested:** the alias "Coke" (→ KO) equals Coca-Cola Consolidated's
  ticker COKE. The alias wins, as before; a separate decision if it matters.
- **Out of scope:** a reference universe of non-corpus names at run time (it would let
  "Constellation Brands" resolve to STZ and be refused as out of corpus). The margin is
  defined so that adding one later only makes resolution stricter.
