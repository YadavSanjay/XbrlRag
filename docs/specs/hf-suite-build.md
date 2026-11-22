# hf-suite-build — build H-F, the sealed held-out-filer suite, blind

H-F is the suite that decides the success bar of the pre-registration
([preregistration.md](../eval/heldout_v2/preregistration.md) §3): generated questions on the
eight drawn filers ([heldout-draw-v2](heldout-draw-v2.md)), keyed from the quarantined
`xbrl_heldout` database by the same builder as the dev and validation suites
(`scripts/eval/build_v2_suite.py`; [v2-suite-build](v2-suite-build.md),
[compound-shape](compound-shape.md)). Today that build is impossible:

- `restrict_to_lexicon` (`src/eval/suites/v2_sources.py`) drops every filer the lexicon does
  not name, and the committed lexicon must never name a held-out filer
  (`configs/eval/v2/lexicon.yaml`, its header). The renderer also needs the filers'
  `period_ends` for the "fiscal year ended" form.
- The builder knows only the splits `dev` and `validation`; the held-out tools key on the tag
  `split:heldout_filers` (`src/eval/core/run_columns.py`, `frontier_sec_tools.py`,
  `regrade.py`) and on `heldout_*` suite names (guide 11).
- The main build raises `WitnessBlocked`, naming each blocked key. H-F is generated blind: no
  person may read a held-out key, so no reviewed resolution can be written for one.
- The filtered FSDS cache holds rows for the development filers only, and no committed tool
  cuts it for another CIK list.
- Logs go to the repository's `logs/`, and the script logs the tickers it leaves out.

This change adds a **held-out mode** to the builder. Decisions (maintainer, 2026-11-10):

1. A **lexicon overlay** (companies and period ends of the held-out filers) is read from a
   path outside the repository and merged in memory. The committed lexicon is never modified.
   A generator, run by the maintainer against `xbrl_heldout`, writes the overlay from the
   database and the public draw file and prints counts only.
2. `--split heldout_filers`: every case is tagged `split:heldout_filers`; the suites are
   `heldout_hf` (main) and `heldout_hf_compound`; there is no validation file; the share is 0.
3. The `covered` universe is every lexicon-nameable filer of the database (development and
   held-out). Only intents that involve at least one held-out filer are kept.
4. Named groups apply the committed `definitions` of `configs/eval/v2/groups.yaml` to the
   committed sector tags plus held-out tags read from a file outside the repository, so a
   held-out Retail filer joins `retailers`.
5. In held-out mode a blocked key drops its candidate as `witness_blocked` in the main build
   too (as the compound build already does), and the build's output reports counts only:
   never a key, ticker, value or question, on the console or in the logs.
6. A committed tool cuts the FSDS quarters for a CIK list into a chosen directory outside the
   repository.
7. A log-directory override, so a held-out build logs into the held-out tree.
8. The dev and validation builds stay byte-identical.

Everything here costs $0: no LLM call. The library functions are tested on synthetic
fixtures; the scripts are contracts (they need the database).

## Requirement

Throughout, "the repository" is the git work tree the code runs from (`REPO_ROOT` of the
scripts), and a **held-out ticker** is an element of the `heldout` argument, stripped and
upper-cased.

### 1. Paths outside the repository

`require_outside(path, repo_root)` returns `Path(path).resolve()` when it lies outside
`Path(repo_root).resolve()`, and raises `ValueError` when it equals the root or lies under
it. The comparison is per path component, after `os.path.normcase` (so `D:/X/Repo2` is
outside `D:/X/Repo`, and on Windows the case of a letter does not matter). Every held-out
input and output path of the scripts goes through it: the overlay, the held-out tags, the
FSDS cut's output directory, the held-out build's `--out-dir` and the log directory.

### 2. The lexicon overlay

`merge_lexicon_overlay(lexicon, path)` reads the YAML file at `path` and returns a new
`Lexicon`:

- The file is a lexicon file (the format `load_lexicon` reads and validates, with the same
  rules for surface strings, tickers and dates) holding only the top-level keys `companies`
  and `period_ends`. Any other top-level key raises `ValueError`.
- A ticker the overlay names (in `companies` or `period_ends`) that the base lexicon already
  names (in its `companies` or `period_ends`) raises `ValueError`: the overlay adds filers,
  it never changes a committed one. A `period_ends` ticker absent from the overlay's own
  `companies` raises `ValueError`.
- The result's `companies` and `period_ends` are the base's entries followed by the
  overlay's; `metrics`, `dimensions` and `universes` are the base's. The base lexicon object
  is not changed.
- An error message names no ticker or surface string of the overlay (it may name the key
  and the count).

### 3. The overlay generator

- `drawn_filers(draw)` takes the parsed draw file (`docs/eval/heldout_v2/heldout_v2_draw.json`
  format, [heldout-draw-v2](heldout-draw-v2.md) requirement 6). Its `scheme` must be
  `xbrlrag-heldout-v2`, and it must hold `strata.S.drawn` and `strata.D.drawn`, else
  `ValueError`. It returns `{"cik": <10-digit>, "symbol", "name"}` for every drawn entry of
  both strata (alternates are not included), sorted by `cik`.
- `lexicon_overlay(drawn, entities, filings)` builds the overlay document:
  - `entities` are `(cik, company_name, trading_symbol)` rows (`entity_master`); `filings`
    are `(cik, accession, form_type, fiscal_year, filed, period_end)` rows, with `filed` and
    `period_end` ISO dates (`period_end` the filing's `DocumentPeriodEndDate`). CIKs compare
    as 10-digit strings.
  - A drawn filer with no entity row, or whose `trading_symbol` is empty or `None`, is left
    out. Otherwise its ticker is the stripped, upper-cased `trading_symbol`; `short` is the
    draw's `name` stripped; `legal` is the stripped `company_name`, or `short` when that is
    empty or `None`. Two drawn filers with one ticker raise `ValueError`.
  - Its period ends: for each fiscal year with an original annual filing (`form_type` in
    `10-K`, `20-F`, `40-F`), the `period_end` of the earliest filed one (then the smallest
    accession). Amendments and other forms are ignored. A filer with no such filing has no
    `period_ends` entry.
  - It returns `{"companies": {ticker: {"short", "legal"}}, "period_ends": {ticker: {year
    (int): "YYYY-MM-DD"}}}`, tickers sorted, years ascending.
- `write_lexicon_overlay(overlay, path)` writes that document as YAML (UTF-8) that
  `merge_lexicon_overlay` reads back to exactly those companies and period ends, and returns
  the path.

**Script (contract, not spec-tested):** `scripts/eval/build_heldout_overlay.py --draw
docs/eval/heldout_v2/heldout_v2_draw.json --out PATH`. It refuses an `--out` inside the
repository (requirement 1), reads `entity_master` and `filing_registry` read-only through
`connect_readonly` (the database comes from the environment: `heldout.ps1` selects
`xbrl_heldout`), and runs `lexicon_overlay` twice: once for the drawn filers, and once as a
**self-check** for every filer of the committed lexicon that the database holds, whose
period ends must equal the committed ones. On any self-check mismatch it writes nothing and
exits 1. It prints counts only: drawn filers, filers written, filers left out, period ends
written, self-check filers and mismatches. It never prints a ticker, name or date.

### 4. Held-out groups

`heldout_groups(groups_doc, dev_tags, heldout_tags)`:

- `groups_doc` is `load_universe_groups`' result; `dev_tags` and `heldout_tags` are
  `load_sector_tags` results (the held-out tags file has the committed sectors file's
  format and lives outside the repository).
- A CIK or ticker in both tag sets raises `ValueError`.
- The groups are `build_groups(merged, groups_doc["definitions"])` over the merged filers
  (`entity_sectors.build_groups`, unchanged).
- For every group, its members that are not held-out tags' tickers must equal the committed
  `groups_doc["groups"][gid]`; otherwise `ValueError` (the committed groups are stale; the
  message names the group id only).
- It returns `{gid: sorted tickers}` in the definitions' order.

### 5. Which intents a held-out build keeps

`involves_heldout(intent, universes, heldout)` is true when a held-out ticker is among
`intent.tickers` or, when `intent.universe` is not `None`, among `universes[intent.universe]`
(an unknown universe raises `ValueError`). So a single-filer intent is kept when its filer is
held out; a screen, peer-relative or compound intent when its universe lists a held-out filer.

### 6. The held-out main build

`build_heldout_suites(inventory, universes, sec, fact_store, phrasebook, lexicon, *, heldout,
quotas, per_intent, salt, witness=None)`:

1. `heldout` must be non-empty, else `ValueError`; every held-out ticker must be in the
   inventory, else `ValueError` whose message gives the count of missing filers and names
   none.
2. Candidates are `enumerate_intents(inventory, universes)` filtered by `involves_heldout`;
   per shape they are walked in `order_candidates` order and keyed exactly as
   `build_v2_suites` keys them (same quotas validation, same drop counting) until the quota is
   met. The manifest's `candidates` counts the filtered candidates.
3. **Witness blocks.** With a witness, each candidate is keyed inside
   `witness.provisional()`; a candidate whose keying meets a blocked key input (the scope's
   list non-empty) is dropped as `witness_blocked`, whatever other reason its keying meets,
   and the walk moves on. The build never raises `WitnessBlocked`, and afterwards
   `witness.raise_if_blocked()` reports nothing the held-out build met.
4. **Resolution use.** With a witness, a reviewed resolution that covers no witness the build
   kept is not an error (the list was reviewed against the development build; H-F meets a
   subset). `manifest["key_resolutions"]` is `check_resolution_use` over the resolutions that
   cover a kept witness (so its `n`, `original`, `reviewed` and `by_bucket` count those), plus
   `"unused"`: the number of resolutions covering none. `key_independence` and
   `key_definitions` are as in `build_v2_suites`.
5. Phrasing is `build_phrased_cases(..., per_intent=per_intent, share=0, salt=salt)`. Every
   case record is the record `build_v2_suites` would emit for the same keyed intent and
   phrasing (requirements 13–14 of v2-suite-build), except that its `suite` is `heldout_hf`
   and its first tag is `split:heldout_filers` (the other six tags unchanged, in order).
6. It returns `{"heldout_filers": suite, "manifest": manifest, "intents": intents}`:
   - the suite is `{"name": "heldout_hf", "version": "1", "split": "heldout_filers",
     "n_cases", "content_hash": content_hash(cases), "cases": [records sorted by id]}`;
   - the manifest has the main manifest's keys with the split `heldout_filers` only:
     `"split": "heldout_filers"`, `"content_hashes": {"heldout_filers": ...}`, `"cases":
     {"heldout_filers": {"total", "by_shape", "by_key_status"}}`, `"intents"` (quota,
     selected, candidates, dropped, dropped_by_shape), `"disagreements"`, `"phrasing":
     {"per_intent", "share": 0, "salt"}`, `"intents_hash"`, `"heldout": {"filers": <number
     of held-out tickers>}`, and with a witness `key_independence`, `key_definitions`,
     `key_resolutions`;
   - `intents` is the intent list (the nine canonical fields, sorted by intent id).
   Nothing depends on the clock, the process or the order of the inventory, universes,
   members or `heldout`.

### 7. The held-out compound build

`build_heldout_compound_suites(...)` takes the same arguments with `quotas` over the two
compound shapes, and does for `build_compound_suites` what requirement 6 does for
`build_v2_suites`: candidates filtered by `involves_heldout`; witness blocks dropped as
`witness_blocked` (unchanged from compound-shape); resolution use as in 6.4; share 0; each
record the one `build_compound_suites` would emit, except `suite` `heldout_hf_compound` and
first tag `split:heldout_filers`; the suite named `heldout_hf_compound`; the manifest as in
6.6.

### 8. Writing

`write_heldout_suites(build, out_dir)` writes `heldout_hf.json`, `heldout_hf_manifest.json`
and `heldout_hf_intents.json`; `write_heldout_compound_suites(build, out_dir)` writes
`heldout_hf_compound.json`, `heldout_hf_compound_manifest.json` and
`heldout_hf_compound_intents.json`. Both use the main writer's format (`json.dumps(doc,
sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"`, UTF-8, `\n` line
ends) and return `{"heldout_filers": path, "manifest": path, "intents": path}`. No other
file is written.

### 9. Counts-only output

`heldout_summary(build)` returns the lines the script prints for a held-out build (main or
compound): per shape the quota, selected, candidate and drop counts by reason; the case
total and counts by key status; the content hash; the number of disagreements; and, with a
witness, the witness counts by status and the resolution counts (including `unused`). No
line holds a ticker, CIK, company name, concept, metric surface form, intent or case id,
question text, or key value: no number other than a count or the `sha256:` hash (so no
decimal point and no run of seven or more digits outside the hash). The held-out builds log
nothing that names a ticker, CIK, value or question at any level.

### 10. Cutting the FSDS quarters

`cut_fsds_tables(raw, ciks)` takes `{"sub", "num", "pre", "tag"}` → the bytes of SEC's
`<table>.txt` from a quarter zip and returns `{"sub", "num", "pre", "tag"}` → bytes:

- each output is the input's header line, then the kept lines byte for byte in input order,
  each line ended by `\n` (empty lines are dropped); columns are found by the header;
- `sub` keeps the rows whose `int(cik)` is the `int` of a requested CIK (any form);
- `num` and `pre` keep the rows whose `adsh` is a kept `sub` row's;
- `tag` keeps the rows whose `(tag, version)` appears in a kept `num` or `pre` row;
- an empty `ciks` raises `ValueError`.

This is the rule the development cut `data/cache/sec_fsds/filtered` follows (Notes).

`cut_fsds(zips, ciks, out_dir)` cuts each quarter zip (named `<yyyy>q<n>.zip`, else
`ValueError`; a zip without one of the four `<table>.txt` raises `FileNotFoundError`) into
`<out_dir>/<yyyy>q<n>/<table>.tsv` and returns `{quarter: {table: kept rows (header not
counted)}}`. The result is read by `fsds.load_tables` like the development cut.

**Script (contract):** `scripts/eval/cut_fsds.py --raw-dir data/cache/sec_fsds/raw
(--cik CIK ... | --ciks-file PATH) --out-dir DIR` refuses an `--out-dir` inside the repository
and prints per quarter the kept row counts only. For H-F the CIK list is every filer of
`xbrl_heldout` (development and held-out), because screens over `covered` witness the
development members too.

### 11. Log directory

- `resolve_log_dir(explicit, environ)` (new module `src/log_dir.py`) returns `Path(explicit)`
  when `explicit` is not `None`, else `Path(environ["XBRLRAG_LOG_DIR"])` when that is set and
  non-empty, else `None`. A relative result raises `ValueError` (a relative directory would be
  anchored at the project root).
- `relocate_log_handlers(cfg, log_dir)` returns a deep copy of the logging config `cfg` in
  which every handler with a `filename` has it replaced by `str(Path(log_dir) /
  Path(filename).name)` (the `{process}` and `{pid}` placeholders kept); other handlers and
  keys are unchanged; `cfg` is not modified. A relative `log_dir` raises `ValueError`.
- **For the implementer:** `configure_logging` gains `log_dir=None`; it applies
  `relocate_log_handlers(cfg, resolve_log_dir(log_dir, os.environ))` before
  `_per_process_filenames` whenever the resolved directory is not `None`. Without the
  argument and the variable, logging is exactly as today. Since `src/__init__.py` configures
  logging on import, the variable (set by the held-out environment profile) is the route that
  covers the whole process.

### 12. The build script in held-out mode (contract)

`scripts/eval/build_v2_suite.py` gains `--split {dev,heldout_filers}` (default `dev`),
`--lexicon-overlay PATH`, `--heldout-tags PATH` and `--log-dir PATH`. With `--split dev`
(the default) every output, console line and log line is exactly what it is today. With
`--split heldout_filers`:

- `--lexicon-overlay`, `--heldout-tags` and `--out-dir` are required and must be outside the
  repository; `--share` must be 0; the active log file (`current_log_file()`) must be outside
  the repository; otherwise exit 2 before any database read.
- The lexicon is `merge_lexicon_overlay(load_lexicon(--lexicon), --lexicon-overlay)`; the
  held-out tickers are the overlay's companies; the inventory is `restrict_to_lexicon` with
  that lexicon; `covered` is every inventory ticker; the named groups are `heldout_groups`
  over `--groups`, its sectors file and `--heldout-tags`.
- It runs `build_heldout_suites` and (with `--compound N`, N > 0)
  `build_heldout_compound_suites` with one shared witness, and writes them with the two
  held-out writers. It writes no `xbrl_v2_*` file.
- It prints `heldout_summary` of each build, the number of filers and of universes, and the
  written paths; it logs the counts of filers and metrics left out, never their names. An
  exception is reported on the console by its type only; its traceback goes to the log file
  (in the held-out tree).

### 13. Nothing else moves

`build_v2_suites`, `build_compound_suites`, their writers, `restrict_to_lexicon`,
`load_lexicon`, `load_universe_groups`, `build_groups` and `KeyWitness` keep their behaviour:
the retired spec tests of v2-suite-build, compound-shape, sector-tags, reviewed-resolutions
and key-statement-witness keep passing (`pytest test/spec`). A held-out build changes nothing
a later development build in the same process computes, and a lexicon merged with an overlay
whose filers are not in the inventory gives a development build byte-identical to the base
lexicon's.

## Acceptance criteria

Fixtures (all synthetic, under `test/spec/hf-suite-build/`): the compound-shape filers
`DEVA`..`DEVE` (made-up CIKs `0000800001`..`0000800005`, December filers, FY2023 and FY2024,
revenue, operating income and net income in USD; `DEVE` FY2024 revenue in EUR only) with the
same companyfacts table, a base lexicon naming `DEVA`, `DEVC` and `DEVE`, an overlay naming
`DEVB` and `DEVD` (with period ends), and a full lexicon naming all five; universes `covered`
(all five) and `pair` (`DEVA`, `DEVC`); the compound-shape statement-witness fixture (DEVB's
two net income keys are `note_unlisted`, blocked); hand-written FSDS tables zipped in the
test; synthetic sector tags and a groups file. Fakes sit only at the I/O seams
(`SecSource`, `FactStoreSource`). Values compare exactly or with `pytest.approx(rel=1e-12)`
(arithmetic on the fixture's numbers).

- **C1**: `require_outside`. A path under the root and the root itself raise `ValueError`; a
  sibling whose name extends the root's (`repo2`) and an unrelated directory are returned
  resolved. Property: for any relative parts, `root/parts` raises and `other/parts` is
  returned. Oracle: requirement 1.
- **C2**: `merge_lexicon_overlay`. Base + overlay equals the full lexicon loaded by
  `load_lexicon` (companies, period ends, metrics, universes); the base object is unchanged;
  an overlay with `metrics` or `universes`, one re-naming a base ticker, one whose period-ends
  ticker has no company, and one with a brace in a name each raise `ValueError` whose message
  names no overlay ticker. Metamorphic: merging two disjoint overlays in either order gives the
  same companies and period ends. Oracle: requirement 2; the full lexicon is written by hand.
- **C3**: the overlay generator. `drawn_filers` on a synthetic draw gives the drawn entries
  of both strata sorted by CIK, no alternate, and refuses a wrong scheme; `lexicon_overlay`
  on hand-written rows gives the hand-derived companies (ticker from the trading symbol,
  short from the draw, legal from the entity, fallback to short) and period ends (earliest
  original annual filing per year; a 10-K/A and a 10-Q ignored), leaves out a filer with no
  symbol, and refuses two filers with one ticker; `write_lexicon_overlay` round-trips through
  `merge_lexicon_overlay`. Metamorphic: permuting the rows changes nothing. Oracle:
  requirement 3, by hand.
- **C4**: `heldout_groups`. With dev tags (DEVA Retail, DEVC Semiconductors) and held-out
  tags (DEVB Retail, DEVD Software), `retailers` is `[DEVA, DEVB]`, `semiconductors`
  `[DEVC]` and a `tickers` group unchanged; a CIK or ticker in both tag sets, and committed
  groups that differ from the dev part, raise `ValueError`. Property: for any assignment of
  held-out industries, each group is the committed members plus exactly the held-out filers
  whose industry the definition selects. Oracle: requirement 4, by hand.
- **C5**: `involves_heldout` and candidate filtering. Hand-made intents are classified per
  requirement 5; with `heldout = [DEVD]` the manifest's candidate counts are lookup 6,
  `compute.ratio` 4, `compute.yoy_growth` 3, `compute.difference` 3, `point_in_time` 3,
  `compute.cagr` 0, `trend` 0, `peer_relative` 25, each screen shape 6, and the compound
  build's `rank_by_ratio` 24 and `leader_growth` 6. Property: for any non-empty held-out
  subset, each shape's candidate count equals the count of `enumerate_intents` candidates the
  test's own restatement of requirement 5 keeps. Oracle: requirement 5, counted by hand.
- **C6**: held-out main cases. With `heldout = [DEVD]`: one suite `heldout_hf` (no validation
  key in the result), every case's `suite` `heldout_hf` and first tag `split:heldout_filers`,
  `run_split` of the cases `heldout_filers`; the DEVD FY2024 revenue lookup's value is
  `200e6`; the manifest records `split`, `share 0`, the salt, `heldout.filers 1`, and counts
  equal to the suite's; the content hash is `sha256` of the canonical cases computed in the
  test; an empty `heldout`, and one naming a filer outside the inventory, raise `ValueError`
  (the latter's message names no ticker). Metamorphic: every held-out record equals the
  development build's record of the same case id (share 0, quotas above every count) with
  `suite` and the split tag replaced; the held-out case ids are exactly the development case
  ids of the intents `involves_heldout` keeps; permuting the inventory, universes and
  `heldout` gives the same result. Oracle: requirement 6, the fixture, and the metamorphic
  comparison with `build_v2_suites`.
- **C7**: held-out compound cases. The same as C6 for `build_heldout_compound_suites` against
  `build_compound_suites` (suite `heldout_hf_compound`, tags `split:heldout_filers` ...
  `compound`); with `heldout = [DEVD]`, every kept intent's universe is `covered`. Oracle:
  requirement 7 and the metamorphic comparison.
- **C8**: witness blocks and resolutions. With a real `KeyWitness` and `heldout = [DEVB]`,
  lookups only: 6 candidates, 4 selected, `dropped {"witness_blocked": 2}` (DEVB net income
  FY2023 and FY2024); the build does not raise; `raise_if_blocked()` afterwards does not
  raise, while a development build on a fresh witness raises `WitnessBlocked`. With DEVB's
  keys resolved `key`: all 6 kept, the net income values `40e6` and `48e6`, and
  `key_resolutions` `n 2`, `original 2`, `reviewed 0`, `unused 0`; with `heldout = [DEVD]`
  and the same resolutions, the build does not raise and records `n 0`, `unused 2`, every
  bucket 0. Compound: a held-out compound build on the same witness drops the blocked
  candidates as `witness_blocked` (3 of the 6 `leader_growth` candidates over `covered`) and
  does not raise. Property: for any lookup quota `q`, `selected == min(q, 4)`, `selected +
  dropped <= 6`, `== 6` whenever `selected < q`, and every drop is `witness_blocked`. Oracle:
  requirements 6.3-6.4, the witness fixture's README, by hand.
- **C9**: writing. The two writers write exactly the three named files each, byte for byte
  `json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"`,
  return the three paths, and `EvalSuite.load` reads the suite; no `xbrl_v2_*` file appears.
  Metamorphic: two builds write identical bytes. Oracle: requirement 8.
- **C10**: counts only. `heldout_summary` of a main and a compound build (with and without a
  witness) is non-empty, states the selected and case counts that the build holds, and no line
  contains a fixture ticker, CIK (padded or not), company name, `us-gaap`, `int-`, a case id,
  a question, a decimal point, or a run of seven digits outside a `sha256:` token; no log
  record emitted at DEBUG during the builds and their refusals contains a ticker, CIK, value
  or question. Property: over random held-out subsets the same holds. Oracle: requirement 9.
- **C11**: FSDS cut. On hand-written tables (two filers in `sub`, a 10-Q among them, `num` and
  `pre` rows of three filings, `tag` rows used and unused, an extra column order) the cut for
  one CIK is the hand-written expected bytes; `cut_fsds` writes `<quarter>/<table>.tsv`,
  returns the hand-counted rows, and `load_tables` reads the result; a bad zip name and a zip
  missing `tag.txt` raise; an empty CIK list raises. Metamorphic: the rows kept for `A ∪ B`
  are the union of those for `A` and for `B`, in input order; padded and unpadded CIKs cut the
  same. Oracle: requirement 10, by hand.
- **C12**: log directory. `resolve_log_dir` prefers the argument, then `XBRLRAG_LOG_DIR`,
  else `None`, and refuses a relative directory; `relocate_log_handlers` moves every file
  handler's `filename` into the directory with its name and placeholders kept, leaves the
  console handler and the input unchanged, and `expand_log_filename` of a relocated name lies
  in the directory. Property: for any handler set, only `filename` values change. Oracle:
  requirement 11.
- **C13**: nothing else moves. A development build (`build_v2_suites` and
  `build_compound_suites`) with the base lexicon merged with an overlay of filers absent from
  the inventory writes the same bytes as with the base lexicon, and the same bytes before and
  after held-out builds on the same inputs; its suites are still `xbrl_v2_dev` /
  `xbrl_v2_validation` with `split:dev` / `split:validation` tags; `restrict_to_lexicon`
  with the merged lexicon keeps the held-out filers and leaves the others' entries as the
  base lexicon does. Oracle: requirement 13, metamorphic.

## Interface

New module `src/eval/suites/heldout_build.py`:

- `require_outside(path: str | Path, repo_root: str | Path) -> Path`
- `merge_lexicon_overlay(lexicon: Lexicon, path: str | Path) -> Lexicon`
- `drawn_filers(draw: Mapping[str, Any]) -> list[dict[str, str]]`
- `lexicon_overlay(drawn: Sequence[Mapping[str, str]], entities: Iterable[Sequence[Any]], filings: Iterable[Sequence[Any]]) -> dict[str, Any]`
- `write_lexicon_overlay(overlay: Mapping[str, Any], path: str | Path) -> Path`
- `heldout_groups(groups_doc: Mapping[str, Any], dev_tags: Mapping[str, Any], heldout_tags: Mapping[str, Any]) -> dict[str, list[str]]`
- `involves_heldout(intent: Intent, universes: Mapping[str, Sequence[str]], heldout: Collection[str]) -> bool`
- `build_heldout_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]], sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook, lexicon: Lexicon, *, heldout: Sequence[str], quotas: Mapping[str, int], per_intent: int, salt: str, witness: KeyWitness | None = None) -> dict[str, Any]`
- `build_heldout_compound_suites(` same arguments `) -> dict[str, Any]`
- `write_heldout_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]`
- `write_heldout_compound_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]`
- `heldout_summary(build: Mapping[str, Any]) -> list[str]`

New module `src/eval/sec_reference/fsds_cut.py`:

- `cut_fsds_tables(raw: Mapping[str, bytes], ciks: Collection[str]) -> dict[str, bytes]`
- `cut_fsds(zips: Sequence[str | Path], ciks: Collection[str], out_dir: str | Path) -> dict[str, dict[str, int]]`

New module `src/log_dir.py` (applied by `configure_logging` in `src/logging_config.py`):

- `resolve_log_dir(explicit: str | Path | None, environ: Mapping[str, str]) -> Path | None`
- `relocate_log_handlers(cfg: Mapping[str, Any], log_dir: str | Path) -> dict[str, Any]`

Changes the implementer makes to existing code (not stubbable): `configure_logging(...,
log_dir=None)` (requirement 11); `scripts/eval/build_v2_suite.py` held-out mode (12); the new
scripts `scripts/eval/build_heldout_overlay.py` (3) and `scripts/eval/cut_fsds.py` (10); any
private refactor of `v2_suite.py` the held-out builds need, provided requirement 13 holds.
The held-out environment profile (`heldout.ps1`, outside the repository) gains
`XBRLRAG_LOG_DIR`; the maintainer edits it.

## Notes

- **H-F build arguments (maintainer's choice, recorded here, not tested):** `--split
  heldout_filers --total 130 --per-intent 3 --share 0 --compound 17 --salt xbrlrag-v2-hf-1
  --groups configs/eval/v2/groups.yaml`, plus the overlay, held-out tags, FSDS cut and
  `--out-dir` in the held-out tree. The salt `xbrlrag-v2-hf-1` is recorded by the maintainer
  in the pre-registration amendment that records the H-F hash. With share 0 the salt does not
  change the split; it is recorded for the manifest and the rebuild.
- **Why a held-out key may be left out.** The pre-registration's K1 lets a blocked key be
  resolved by a reviewed entry or left out. No person may read a held-out key, so in held-out
  mode every unresolved block is left out (`witness_blocked`), counted in the manifest, as the
  compound build already does (compound-shape, "Witness blocks").
- **Why unused resolutions are not an error in held-out mode.** The committed
  `key_resolutions.yaml` was reviewed against the development build, which keys every
  development filer. H-F keys only intents that involve a held-out filer, so most entries
  cover nothing it keys; the stale-entry check of reviewed-resolutions requirement 3 would
  fail every H-F build. The counts (`n` used, `unused`) are recorded instead.
- **The FSDS cut rule** (requirement 10) was checked against the committed development cut:
  applied to `data/cache/sec_fsds/raw/2024q1.zip` with the 32 CIKs of
  `data/cache/sec_fsds/filtered/2024q1/sub.tsv`, it reproduces all four filtered files byte
  for byte (sub 33 lines, num 16,640 rows, pre 3,967, tag 903; 2026-11-10). The spec tests
  read only synthetic tables.
- **The overlay self-check** guards the period-end column choice: the development filers'
  period ends in the committed lexicon are their `DocumentPeriodEndDate`s, so the generator's
  query reproduces them for every development filer in `xbrl_heldout` or writes nothing.
- **Held-out tags** use the committed sectors file's format (`as_of`, `source`, `filers`
  with `cik`, `ticker`, `gics_sector`, `industry`). The draw file gives the GICS sector; the
  industry label is set by the maintainer, by the committed file's rules. Neither is key data.
- **Hard rules kept by the tests:** no fixture names a real held-out filer; no test reads
  `data/`, the draw file, the held-out tree or a database.
- **Out of scope:** running the H-F build (task E4), the H-F hash (a pre-registration
  amendment), H-X, and any held-out run.
