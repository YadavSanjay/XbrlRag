# sector-tags — GICS sector and industry tags per filer, and named universe groups

Phase 2 of the v2 plan (docs/design/XbrlRag_v2_Plan.md §4.5, "Sector and industry tags on
`entity_master`"). Peer-relative questions ("among retailers", "the semiconductor median")
need a universe to rank over. The evaluation semantics (docs/design/XbrlRag_Eval_Semantics.md
S3) say a named group is resolved from a committed groups file with an as-of date and
intersected with the corpus; today no such file exists, and `entity_master` has no sector.

## Requirement

1. **Sector tags are a committed, validated config.** `configs/entities/sectors.yaml` gives,
   per filer CIK, its ticker, its GICS sector and a reviewed industry label, with a file-level
   as-of date and source. It is loaded through a typed loader that fails loud: a malformed
   CIK, a sector that is not one of the eleven GICS sector names, a duplicate CIK or ticker,
   an empty industry, a missing source, a bad as-of date or an unknown key raises
   `ValueError`. Format:

   ```yaml
   as_of: "2026-10-29"
   source: "S&P 100 constituents (docs/eval/heldout_v2/sp100_constituents.csv), sectors reviewed"
   filers:
     - cik: "0001045810"
       ticker: NVDA
       gics_sector: Information Technology
       industry: Semiconductors
     - cik: "0001046179"
       ticker: TSM
       gics_sector: Information Technology
       industry: Semiconductors
       note: "Not an S&P 100 constituent (foreign private issuer); GICS from MSCI, reviewed by hand"
   ```

   `note` is optional in the format; the committed file carries one on every filer that is
   not in the committed S&P 100 list. The GICS sector names are exactly (case-sensitive):
   Communication Services, Consumer Discretionary, Consumer Staples, Energy, Financials,
   Health Care, Industrials, Information Technology, Materials, Real Estate, Utilities.
2. **The tags cover the development universe and nothing held out.** The committed file
   covers every filer in the corpus (the 15 below) and every wave-1 filer
   (`configs/eval/v2/heldout_exclusions.yaml` `wave1`); it may also cover wave-2 filers. It
   never names a held-out candidate (any `candidates` symbol of
   `docs/eval/heldout_v2/heldout_v2_pool.json`, which includes the drawn filers and their
   alternates): the held-out filers stay out of every development file.
3. **Named groups are derived from the tags, deterministically.** `configs/eval/v2/groups.yaml`
   holds an as-of date, the sectors file it was built from, a `definitions` mapping (group id →
   exactly one rule: `sectors: [...]`, `industries: [...]` or `tickers: [...]`) and the
   resolved `groups` mapping (group id → sorted tickers). `build_groups` computes the
   resolved groups from the tags and the definitions; the committed `groups` equal its
   output. A generator script (implementer) rewrites `groups` from the definitions.

   ```yaml
   as_of: "2026-10-29"
   sectors_file: configs/entities/sectors.yaml
   definitions:
     semiconductors: {industries: [Semiconductors]}
     retailers: {industries: [Retail]}
     big_tech: {tickers: [AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA]}
   groups:
     semiconductors: [AMD, AVGO, INTC, MU, NVDA, QCOM, TSM, TXN]
     retailers: [AMZN, COST, HD, WMT]
     big_tech: [AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA]
   ```

4. **A check keeps the groups honest.** `check_groups` reports every group whose committed
   members differ from `build_groups`, every member that is not a corpus filer, and every
   group id the lexicon cannot phrase (absent from the lexicon's `universes`, or with no
   phrase), and the reserved id `covered` (built from the corpus, never a group).
5. **`entity_master` gets nullable `gics_sector` and `industry` columns**, filled by an
   idempotent step that is **not** part of the global `layer0-populate` re-run (that step
   rewrites identity; docs/specs/identity-and-stand-in.md). The step's decisions come from a
   pure planner: it updates only rows whose stored pair differs from the tags, clears the pair
   of a row the tags no longer name, and never inserts a row (identity is ingest's).
6. **A reader resolves a named group to CIKs**, for the retriever's screen to use later
   (not wired by this change).

## Acceptance criteria

- **C1**: `load_sector_tags(path)` on a valid file returns
  `{"as_of": "<YYYY-MM-DD>", "source": <str>, "filers": {cik: {"ticker", "gics_sector",
  "industry"}}}`, exact (the note is not returned; surrounding whitespace of string values is
  trimmed). Property: the result does not depend on the order of the `filers` entries.
  Oracle: Requirement 1; synthetic files written by the test.
- **C2**: `load_sector_tags` raises `ValueError` for: a CIK that is not exactly ten digits; a
  sector not among the eleven names (including a wrong-case one such as "information
  technology"); a duplicate CIK; a duplicate ticker; an empty or whitespace-only industry;
  a missing or empty `source`; a missing `as_of` or one that is not an ISO calendar date
  (`YYYY-MM-DD`); an unknown key at
  either level. Property: any malformed CIK and any string outside the eleven names is
  rejected. Oracle: Requirement 1 (fail loud on config errors).
- **C3**: `build_groups(tags, definitions)` returns group id → the sorted, de-duplicated
  tickers of the filers the rule selects (`sectors`: GICS sector in the list; `industries`:
  industry in the list; `tickers`: the listed tickers). It raises `ValueError` when a rule
  has zero or two rule keys, an unknown key, a `tickers` entry that is not a filer in the
  tags, or resolves to no member. Property: the result equals the selection computed
  independently in the test, and is unchanged when the filers or definitions are reordered.
  Oracle: Requirement 3.
- **C4**: `load_universe_groups(path)` returns `{"as_of", "sectors_file", "definitions",
  "groups"}` for a valid file, with each group's members as a list. It raises `ValueError`
  when: `as_of` is not an ISO date; a group id is not `^[a-z][a-z0-9_]*$` or is `covered`;
  the ids of `definitions` and `groups` differ; a group lists a ticker twice or no ticker.
  Property: any id outside the pattern is rejected. Oracle: Requirement 3 and S3.
- **C5**: `check_groups(groups_doc, tags, corpus_tickers, lexicon_universes)` returns `[]`
  for a consistent setup; otherwise one problem string per fault, each naming the group id
  (and, for a member outside the corpus, the ticker): a group differing from
  `build_groups`, a member not in `corpus_tickers`, an id missing from `lexicon_universes`
  or mapped to no phrase, the id `covered`. Metamorphic: removing any one member's ticker
  from the corpus yields a problem naming that ticker; adding the id's phrases back removes
  the lexicon problem. Oracle: Requirement 4.
- **C6**: `resolve_group_ciks(group_id, groups, tags)` returns the CIKs of the group's
  members, sorted ascending. It raises `ValueError` for an unknown group id, for `covered`,
  and for a member ticker absent from the tags. Property: the result has one CIK per member
  and each CIK's ticker in the tags is a member. Oracle: Requirement 6.
- **C7**: `plan_sector_tag_updates(tags, rows)`, with `rows` the stored `entity_master`
  `{"cik", "gics_sector", "industry"}` rows, returns `(cik, gics_sector, industry)` tuples
  sorted by CIK: the tags' pair for a row whose stored pair differs, `(cik, None, None)` for
  a row the tags do not name whose pair is not already null, and nothing for a CIK that is
  not in `rows`. Property: applying the plan to the rows and planning again returns `[]`
  (idempotent), and the plan does not depend on the order of `rows`. Oracle: Requirement 5.
- **C8**: The committed `configs/entities/sectors.yaml` loads; covers the 15 corpus filers
  below with these CIKs and every wave-1 filer with its CIK in the pool file; names no
  held-out candidate; names only corpus, wave-1 or wave-2 filers; gives every filer on the S&P
  100 list the list's sector, TSM "Information Technology", CEG and VST "Utilities"; carries a
  non-empty `note` on every filer not on the list; and gives the industries
  Semiconductors (AMD, AVGO, INTC, MU, NVDA, QCOM, TSM, TXN), Retail (AMZN, COST, HD, WMT),
  Pharmaceuticals (LLY, MRK) and Railroads (UNP). Exact. Oracle: the committed constituents
  list, the pool file and SEC EDGAR CIKs (Notes).
- **C9**: The committed `configs/eval/v2/groups.yaml` loads; its groups equal
  `build_groups(load_sector_tags(), definitions)`; it contains `semiconductors` = AMD, AVGO,
  INTC, MU, NVDA, QCOM, TSM, TXN, `retailers` = AMZN, COST, HD, WMT and `big_tech` = AAPL,
  AMZN, GOOGL, META, MSFT, NVDA, TSLA (exact); every member is a filer of the committed sectors file;
  and `check_groups` against the committed lexicon's universes, with the sectors file's
  tickers as the corpus, returns `[]` (so each group id has lexicon phrases). Oracle:
  Requirement 3; the industry tags of C8; big tech as the five companies named in the
  definition (Notes).

## Interface

- `src.retriever.xbrl.entity_sectors.load_sector_tags(path: Path | None = None) -> dict[str, Any]`
  (`None` loads `configs/entities/sectors.yaml`)
- `src.retriever.xbrl.entity_sectors.build_groups(tags: dict[str, Any], definitions: dict[str, dict[str, list[str]]]) -> dict[str, list[str]]`
- `src.retriever.xbrl.entity_sectors.load_universe_groups(path: Path | None = None) -> dict[str, Any]`
  (`None` loads `configs/eval/v2/groups.yaml`)
- `src.retriever.xbrl.entity_sectors.check_groups(groups_doc: dict[str, Any], tags: dict[str, Any], corpus_tickers: set[str], lexicon_universes: Mapping[str, Sequence[str]]) -> list[str]`
- `src.retriever.xbrl.entity_sectors.resolve_group_ciks(group_id: str, groups: dict[str, list[str]], tags: dict[str, Any]) -> list[str]`
- `src.ingest.sec_edgar.ingest.sector_tags.plan_sector_tag_updates(tags: dict[str, Any], rows: list[dict[str, Any]]) -> list[tuple[str, str | None, str | None]]`

## Notes

- **Corpus filers (entity_master, read-only, 2026-10-29), ticker → CIK:** AAPL 0000320193,
  AMD 0000002488, AMZN 0001018724, AVGO 0001730168, CEG 0001868275, GOOGL 0001652044,
  INTC 0000050863, META 0001326801, MSFT 0000789019, MU 0000723125, NVDA 0001045810,
  ORCL 0001341439, TSLA 0001318605, TSM 0001046179, VST 0001692819 (SEC EDGAR CIKs). TSM,
  CEG and VST are not in the committed S&P 100 list; INTC is. Wave-1 and wave-2 CIKs are in
  `docs/eval/heldout_v2/heldout_v2_pool.json` `excluded` (from SEC `company_tickers.json`).
- **Sectors** come from the committed constituents list (GICS, as of 2026-09-21). For filers
  not on it: TSM is GICS Information Technology (Semiconductors & Semiconductor Equipment);
  CEG and VST are GICS Utilities. Industry labels are reviewed short labels, not GICS
  sub-industry names: "Retail" spans GICS's Consumer Discretionary and Consumer Staples
  Distribution & Retail industry groups (AMZN, HD; WMT, COST); MCD is a restaurant, not a
  retailer.
- **big_tech** is the maintainer's choice (2026-10-29): the "Magnificent Seven", Apple,
  Amazon, Alphabet, Meta, Microsoft, Nvidia and Tesla, defined by explicit tickers, not a GICS
  category.
- **Implementer work not locked here:** the DDL (`ALTER TABLE entity_master ADD COLUMN IF
  NOT EXISTS gics_sector TEXT`, `industry TEXT`, in `scripts/ddl/layer_0/entity_master.sql`);
  a CLI (`python -m src.ingest.sec_edgar.ingest.sector_tags`) that reads `entity_master`,
  applies `plan_sector_tag_updates` in one transaction and logs each change, run after an
  ingest, separately from `bootstrap_db.py`'s `layer0-populate` (its upsert lists its own
  columns, so it never touches the new ones); the generator script that rewrites `groups`
  in `groups.yaml` from its `definitions` and checks it against the live corpus with
  `check_groups`; the lexicon's `universes` phrases for each group id
  (`configs/eval/v2/lexicon.yaml`); and `scripts/eval/build_v2_suite.py --groups` reading
  `groups.yaml` through `load_universe_groups` (today it expects a flat id → tickers map).
- **Later step (not this change):** the screen resolves a named universe through
  `resolve_group_ciks`, intersects it with the filers that have the period, and the answer
  states the universe and names the exclusions (S3).
- The held-out database (`xbrl_heldout`) gets no tags from this file; if held-out questions
  need groups, that is a separate, quarantined file.
