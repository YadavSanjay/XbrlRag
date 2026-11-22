# hx-authoring — author, check, build and seal the hand-written external set H-X

Part of the v2 evaluation ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.1 and decision 2 in
§10). H-X is **75 questions written by the maintainer alone**: 15 each on cross-filer,
cross-period, compound, honesty and text. They cover about 30 development filers (the
current 15 plus wave 1) and **never a held-out filer**. H-X lives in its own file; set B
and its tooling ([set-b-tooling](set-b-tooling.md)) stay exactly as they are.

This change extends the set-B authoring tooling with new modules, so the maintainer can
write H-X, confirm its numeric answer keys **with SQL only** (no pipeline, no LLM), turn
it into an `EvalCase` suite and seal it with set B's seal. As with set B, the questions
are only useful while the system's author never sees them: **no output and no error of
this tooling contains question, answer, passage, note or amount text, or a ticker.**
Messages carry case ids, field paths, statuses, kinds, sub-types and counts only.

Policies the keys follow are the evaluation semantics
([XbrlRag_Eval_Semantics.md](../design/XbrlRag_Eval_Semantics.md)): S1 (point-in-time:
as originally reported vs latest), S3 (a cross-filer key names its universe and its
exclusions), S4 (CAGR), S6 (full values, explicit scale).

## Requirement

### The authoring file

1. **Loading** reuses set B's `load_authoring(text)` (a top-level `cases:` list). Amounts
   are written and parsed with set B's `parse_amount(raw, scale)`, and agreement uses set
   B's rule (`abs(v - value) <= max(half_unit, 1e-9 * abs(value))`), through set B's
   `find_classes`. `unit` is one of set B's units (`USD`, `TWD`, `pure`, `shares`,
   `USD/shares`); `scale` one of set B's scales.
2. **Every case** has `id` (non-empty str, unique), `question` (non-empty str), `kind`
   (one of `cross_filer`, `cross_period`, `compound`, `honesty`, `text`) and an optional
   `note` (str, never printed, never copied). `cross_filer`, `cross_period` and
   `honesty` also need a `subtype`. An **amount item** is a mapping `{expected, scale?,
   accession?}`; an `accession` is a string matching `^\d{10}-\d{2}-\d{6}$`. A fiscal
   year (`fy`) is an int in 2022–2026. Unknown keys are ignored.
3. **`cross_filer`** — `subtype` one of `screen_top`, `screen_bottom`, `count`, `sum`,
   `median`, `peer_relative`; `fy`; `unit`; optional `concept` and `member` hints (str);
   - `universe`: a list of at least 2 distinct tickers, the filers the question covers
     (S3);
   - `exclusions` (optional, default empty): a list of `{ticker, reason}`, `reason` one
     of `not_filed`, `not_tagged`, `currency`, `other`. Each ticker is in `universe`,
     each at most once. The **ranked filers** are `universe` minus the exclusions; there
     must be at least one;
   - `rows`: a list of amount items, each with a `ticker` (distinct, a ranked filer);
   - `screen_top` / `screen_bottom`: at least 1 row, in rank order — the rows' parsed
     values are non-increasing for `screen_top` and non-decreasing for `screen_bottom`;
   - `count`: `expected` is a non-negative YAML int equal to the number of rows (rows may
     be empty): the filers counted;
   - `sum`: at least 2 rows, and `expected` + optional `scale`;
   - `median`: the rows' tickers are exactly the ranked filers, and `expected` + `scale?`;
   - `peer_relative`: `ticker` (the subject, one of the rows), the rows' tickers are
     exactly the ranked filers, and `expected` is the subject's rank, a positive YAML int
     at most the number of rows (1 = highest value).
4. **`cross_period`** — `subtype` one of `trend`, `cagr`, `point_in_time`; `ticker`;
   `unit`; optional `concept` and `member`;
   - `trend`: `rows`, at least 2 amount items each with a distinct `fy`;
   - `cagr`: `rows`, exactly 2 amount items with distinct `fy`, and `expected` +
     `scale?` (usually written with `%`);
   - `point_in_time` (S1): `fy`, and two amount items `as_originally_reported` and
     `latest`, each **requiring** an `accession`; the two accessions differ.
5. **`compound`** — `ranking`: a mapping with the `cross_filer` fields whose `subtype` is
   `screen_top` or `screen_bottom`; `dependent`: a mapping with a `kind` (`cross_filer`,
   `cross_period` or `text`) and that kind's fields (no `id`, no `question`). Every
   ticker the dependent names is one of the ranking's row tickers.
6. **`honesty`** — `subtype` one of `ambiguous`, `false_premise`, `decline`; optional
   `tickers` (list of str) and `fy`;
   - `ambiguous`: `assumptions`, a list of at least 1 non-empty str (the assumption the
     answer must state);
   - `false_premise`: `correction`, a non-empty str (the corrected fact);
   - `decline`: `reason`, one of `out_of_corpus`, `out_of_period`, `not_tagged`,
     `out_of_scope`.
7. **`text`** — `ticker`, `fy`, `reference_answer` (non-empty str), `key_points` (at
   least 1 non-empty str), `passages`: at least 1 `{accession, quote}` (`quote` a
   non-empty str: the supporting span, quoted from that filing).

### Validation (`validate_hx`)

8. **`validate_hx(cases, dev_filers, heldout_filers)` returns `{"errors": [...],
   "warnings": [...], "mix": {...}}`.** `dev_filers` and `heldout_filers` are passed in
   as data (sets of tickers); if they intersect it raises `ValueError` before checking.
   - Every **error** is a string `"<id>: <field path>: <reason>"` (`#<index>` when the
     id is missing or not a usable string). The field path names the field, with list
     indices and nested mappings written as in `rows[2].expected`,
     `ranking.universe[1]`, `dependent.rows[0].fy`. One error per problem; every rule in
     requirements 2–7 is an error when broken (missing or wrong-typed field, unknown
     kind/subtype/unit/scale/reason, an amount `parse_amount` rejects, a bad accession,
     an `fy` out of range, a duplicate id, too few rows, wrong order, a wrong count, a
     rank out of range, rows that don't cover the ranked filers, an exclusion outside
     the universe, equal point-in-time accessions, a dependent filer outside the
     ranking).
   - **Which path.** A field-level problem names that field (`fy`, `rows[1].expected`,
     `passages[0].accession`, `latest.accession` for equal point-in-time accessions). A
     list-level problem names the list: too few or too many rows, rows out of rank order,
     rows that don't cover the ranked filers, and no ranked filer left are `rows`
     (the last `exclusions`); empty `assumptions`, `key_points` or `passages` name that
     list. A repeated entry names the later one (`rows[2].ticker`, `rows[1].fy`,
     `exclusions[1].ticker`); an entry outside its set names the entry
     (`exclusions[0].ticker` outside the universe, `rows[0].ticker` not a ranked filer,
     `dependent.universe[0]` outside the ranking's rows). A wrong `count`, a rank out of
     range and a non-int count or rank are `expected`; a subject not among the rows is
     `ticker`. The duplicate-id error is `id`. A ticker in the wrong filer set names its
     position (`universe[2]`, `ranking.rows[0].ticker`, `tickers[0]`).
   - **Filers.** Every ticker a case names (`ticker`, `tickers`, `universe`,
     `exclusions[].ticker`, `rows[].ticker`, and the same inside `ranking` and
     `dependent`) is checked: one in `heldout_filers` is an error whose reason contains
     `held-out filer`, always; one in neither set is an error whose reason contains `not
     a development filer`, except in an `honesty` `decline` case with reason
     `out_of_corpus`.
   - **No values in messages.** An error or warning never contains any value from the
     case except its id: no question, note, answer, assumption, correction, key point,
     passage, amount, accession or ticker. (This is why unknown values are not echoed.)
   - **Warnings** (whole-set, no id prefix): one per kind whose count is not 15, naming
     the kind and its count.
   - **`mix`:** `{"kind": {k: n for each of the 5 kinds, 0 included}, "subtype":
     {"<kind>.<subtype>": n}, "filers": <distinct tickers named, over all cases>,
     "total": len(cases)}`. A case counts under its kind when the kind is one of the five,
     and under its subtype when the subtype is valid for that kind, whatever else is
     wrong with it.

### Checking answer keys (`resolve_hx_case`)

9. **Seam.** `HxKeyFactStore` (in `src/eval/set_b/hx_base.py`) extends set B's
   `KeyFactStore` with `facts_in_filing(ticker, accession, fiscal_year)`: the candidates
   (set B's keys) that filing `accession` reports for the filer's fiscal year
   `fiscal_year`, current or comparative column. `facts_for(ticker, fiscal_year)` keeps
   set B's meaning.
10. **Items.** `resolve_hx_case(case, store)` resolves each amount item with set B's
    `find_classes(candidates, parse_amount(expected, scale), unit, dimensional, concept,
    member)`, where `dimensional` is true exactly when the case (or part) has a `member`.
    The candidates are `store.facts_in_filing(ticker, accession, fy)` when the item has
    an `accession`, else `store.facts_for(ticker, fy)`. An item's status is `verified`
    with exactly 1 class, `ambiguous` with more, `not_found` with none. Each distinct
    `facts_for` and `facts_in_filing` argument tuple is fetched at most once per call.
    - `cross_filer` rows: the row's `ticker`, the case `fy`; item names `rows[i]`.
    - `cross_period` `trend` / `cagr` rows: the case `ticker`, the row's `fy`; names
      `rows[i]`. `point_in_time`: the case `ticker` and `fy`; names
      `as_originally_reported`, `latest`.
    - `compound`: the ranking's items named `ranking.rows[i]`, then the dependent's
      named `dependent.<name>`. A `text` dependent has no items.
    - Names are positional: never a ticker or a value.
11. **The arithmetic** runs on the rows' written (parsed) values:
    - `aggregate(op, values, subject=None)` over `{name: value}`: `sum` the sum (`0.0`
      when empty); `median` the median (mean of the two middle values for an even count;
      `None` when empty); `count` the number of values; `rank` `1 +` the number of values
      strictly greater than `values[subject]` (ties share a rank; a missing subject raises
      `ValueError`). An unknown op raises `ValueError`.
    - `cagr(start, end, years)` is `(end / start) ** (1 / years) - 1` (S4), `None` unless
      `start > 0`, `end > 0` and `years > 0`.
    - `sum`, `median`, `cagr` (years = later `fy` − earlier `fy`, start = the earlier
      row): agree when `abs(r - expected) <= max(expected_half_unit, 1e-3 *
      abs(expected))`, as set B's compute check. `count` and `peer_relative` (`rank`
      with the case `ticker` as subject, values keyed by row ticker): agree when equal to
      the int `expected`. `None` never agrees.
12. **Status and result.** `resolve_hx_case` returns `{"id", "kind", "status", "items":
    [{"name", "status", "classes"}], "classes": [...]}`.
    - `text` and `honesty`: `status` is `skipped`, `items` and `classes` empty, and the
      store is never called.
    - Otherwise `status` is the first non-`verified` item status (in item order); else
      `arith_mismatch` when the case (or, for `compound`, the dependent part) has an
      arithmetic check that does not agree; else `verified`.
    - `classes` holds one entry per item, in item order: the item's single class when it
      is `verified`, else `[]`.
    - Nothing in the result is a question, answer, passage, amount, accession or ticker.
13. **The report.** `render_hx_report(resolutions)` returns text, every line ending in
    `\n`:
    - the line `H-X answer-key check`, then an empty line;
    - per resolution, in input order, one line `<id>  <kind>  <status>  items=<n>`
      (fields separated by whitespace; it starts with the id), and, when the status is
      neither `verified` nor `skipped`, one line per item whose status is not
      `verified`, indented by 4 spaces: `<name>: <status>, <n> class(es)`;
    - an empty line, `total: <n>`, then one `<status>: <n>` line per status present, in
      the order `verified`, `ambiguous`, `not_found`, `arith_mismatch`, `skipped`, then
      any other, sorted.

    Nothing else from the resolutions is printed (an unknown key, such as a stray
    `question`, never is).

### Building and sealing (`build_hx_suite`)

14. **`build_hx_suite(cases, resolutions, allow_unverified=False)` returns** `{"name":
    "heldout_x", "version": "v1", "n_cases": n, "cases": [...]}`.
    - A case whose resolution status is neither `verified` nor `skipped` (or which has no
      resolution) raises `ValueError` naming the ids only. With `allow_unverified`, those
      cases are left out and their authoring ids go, sorted, in `"excluded"`.
    - Cases are sorted by `id`. Each case dict has the **common** fields: `id` = `"hx-"`
      + the authoring id; `question` (verbatim); `suite: "heldout_x_v1"`; `origin:
      "handwritten:h_x"`; `tags`: `["split:external", <kind>]` plus `<subtype>` when the
      kind has one. `note` is never copied. Then by kind, where `ids` is each item's
      class's first fact id and `classes` the items' classes, in item order:
    - `cross_filer` (`fy`, `universe` sorted, `expected_exclusions` = the exclusions as
      `{ticker, reason}` sorted by ticker, `expected_fact_ids` = ids, `fact_id_classes` =
      classes):
      - `screen_top` / `screen_bottom`: `kind: "screen"`, `unit`, `expected_rows` =
        `[{"entity": ticker, "value": parsed}]` in authoring order, `order_sensitive:
        true`, `top_n` = number of rows, `rel_tol: 1e-4`;
      - `count`: `kind: "compute"`, `expected_value` = float(expected), `unit: "pure"`,
        `rel_tol: 0.0`;
      - `sum` / `median`: `kind: "compute"`, `expected_value` parsed, `unit`, `rel_tol:
        1e-2`;
      - `peer_relative`: as `count`, plus `ticker` = the subject.
    - `cross_period` (`ticker`, `expected_fact_ids`, `fact_id_classes`):
      - `trend`: `kind: "breakdown"`, `fy` = the latest row `fy`, `expected_rows` =
        `[{"entity": "FY<fy>", "value": parsed}]` sorted by `fy`, `order_sensitive:
        false`, `rel_tol: 1e-4` (no `unit`, as set B's breakdown);
      - `cagr`: `kind: "compute"`, `fy` = the later row `fy`, `expected_value` parsed,
        `alt_values: [expected_value * 100]` when `expected` was written with `%` else
        `[]`, `unit: "pure"`, `rel_tol: 1e-2`;
      - `point_in_time`: `kind: "breakdown"`, `fy`, `expected_rows` = `[{"entity":
        "as_originally_reported", "value": ...}, {"entity": "latest", "value": ...}]`,
        `order_sensitive: false`, `rel_tol: 1e-4`, `accessions:
        {"as_originally_reported": ..., "latest": ...}`.
    - `text`: `kind: "narrative"`, `ticker`, `fy`, `expected_text` = `reference_answer`,
      `key_points` (as written), `passages` = `[{"accession", "quote"}]` in authoring
      order.
    - `honesty`: `kind: "narrative"`, `require_citation: false`, plus `tickers` (sorted)
      and `fy` when given, and `assumptions` (ambiguous), `expected_text` = `correction`
      (false_premise) or `decline_reason` = `reason` (decline).
    - `compound`: the common fields, the **ranking**'s own fields (as a `cross_filer`
      case of its subtype, built from the `ranking.*` items), and `parts`: `[{"part":
      "ranking", ...ranking fields}, {"part": "dependent", ...dependent fields}]`, where
      the dependent's fields are those of its kind (and subtype) built from the
      `dependent.*` items. "Fields" here means everything but the common fields.
15. **The seal** is set B's: `canonical_bytes(suite)` and `sha256_hex(data)` from
    `src.eval.set_b.core`, unchanged.

The CLI (`scripts/eval/hx.py`: `validate`, `check-keys`, `build`, `seal`, `verify`,
mirroring set B's), the Postgres `HxKeyFactStore` and the authoring template
(`configs/eval/hx_template.yaml`, one fictional example per kind and subtype, ticker
`EXAMPLE`) are the implementer's and are not spec-tested.

## Acceptance criteria

Fakes sit only at the I/O seam: a fake `HxKeyFactStore` returning synthetic candidates and
recording its calls. Every case is made up in the test; tickers are fictional
(`DEVA`, `DEVB`, ..., held-out `HOLDA`, ...); amounts are chosen by hand.

- **C1**: `validate_hx` accepts valid cases and reports the mix and warnings.
  - One valid case of every kind and subtype gives no errors; `mix` counts are those of
    the hand-built set; `filers` is the distinct tickers counted by hand.
  - With 15 of each kind, no warnings; with 14 of one kind, exactly one warning, naming
    that kind and 14 and nothing case-specific.
  - Overlapping `dev_filers` / `heldout_filers` raise `ValueError`.

  Property: over random sets of valid cases, `errors` is empty, `mix.total` is the case
  count and the kind counts sum to it. Oracle: requirements 2–8.
- **C2**: `validate_hx` rejects every broken rule with the id and field path.
  - A table of minimal bad cases, one per rule in requirements 2–7 (per kind and
    subtype), each giving an error that starts with `"<id>: <field path>"` as stated in
    the test; a missing id gives `#<index>`; a duplicate id is reported.
  - A held-out ticker in any ticker position (including `ranking.` and `dependent.`)
    is an error, also in a `decline` / `out_of_corpus` case; a non-development ticker is
    an error except there.

  Metamorphic: making a valid case's question, note, answer texts, passages, amounts and
  tickers into distinctive strings (and breaking one rule) leaves none of them in any
  message. Oracle: requirement 8.
- **C3**: `aggregate` and `cagr`.
  - `sum`, `median` (odd and even), `count`, `rank` with ties, by hand; empty inputs;
    missing subject and unknown op raise.
  - `cagr(100, 121, 2) == 0.1` (within 1e-12); non-positive start/end or years → `None`.

  Property: `rank` of the maximum is 1; `cagr(s, s * (1 + g) ** n, n)` returns `g`
  (rel 1e-9); `median` is invariant under shuffling. Oracle: requirement 11 and S4.
- **C4**: `resolve_hx_case` for `cross_filer`.
  - Screen rows each read `facts_for(row ticker, fy)`, verified to their classes; one
    row's amount missing gives `not_found` and its item; a duplicate value gives
    `ambiguous`.
  - `sum`, `median`, `count`, `peer_relative` verified, and each with arithmetic off gives
    `arith_mismatch` (the arithmetic is shown in the test).
  - A row with `accession` reads `facts_in_filing` instead; a `member` restricts to
    dimensional candidates with that label.

  Metamorphic: shuffling every candidate list leaves the result unchanged. Oracle:
  requirements 9–12.
- **C5**: `resolve_hx_case` for `cross_period`, `compound`, `text` and `honesty`.
  - `trend` reads each row's year; `cagr` verified and off; `point_in_time` reads the two
    filings through `facts_in_filing` for the case `fy`, and a value only in the other
    filing is `not_found`.
  - `compound`: item names prefixed `ranking.` / `dependent.`; a failing ranking row
    gives its status first; a `text` dependent adds no items.
  - `text` and `honesty`: `skipped`, empty items, no store call.
  - Each fetch tuple is fetched once per call.

  Property: over random distinctive question/answer/passage/amount strings, the
  JSON-serialised result contains none of them nor any ticker. Oracle: requirements
  9–12.
- **C6**: `render_hx_report`.
  - One line per resolution starting with its id; the extra item lines only for failing
    items; `total:` and per-status counts.
  - None of the case's texts, amounts or tickers appears (built from resolutions of
    cases with distinctive strings).

  Property: for random resolutions, the line count is `n + failing items + 1 + statuses`
  (plus the fixed header and blank lines stated in the test). Oracle: requirement 13.
- **C7**: `build_hx_suite` with set B's seal.
  - The exact case dict for one case of each kind and subtype, built by hand.
  - An unverified case raises naming only the id; with `allow_unverified` it's left out
    and listed; `skipped` cases are built.
  - Every built case loads with `EvalCase.from_dict` (its `kind` is an `EvalCase` kind).

  Property: building from a shuffled case list gives identical `canonical_bytes` and
  `sha256_hex`. Oracle: requirements 14 and 15.

## Interface

New, stubbed in the spec PR. New files carry no module docstring (the stub lint allows
none). Everything is a plain `dict`.

`src/eval/set_b/hx_base.py`:
- `class HxKeyFactStore(KeyFactStore)` with
  `@abstractmethod facts_in_filing(self, ticker: str, accession: str, fiscal_year: int) -> list[dict]`.

`src/eval/set_b/hx.py`:
- `validate_hx(cases: list[dict], dev_filers: frozenset[str], heldout_filers: frozenset[str]) -> dict`
- `aggregate(op: str, values: dict[str, float], subject: str | None = None) -> float | None`
- `cagr(start: float, end: float, years: int) -> float | None`
- `resolve_hx_case(case: dict, store: HxKeyFactStore) -> dict`
- `render_hx_report(resolutions: list[dict]) -> str`
- `build_hx_suite(cases: list[dict], resolutions: list[dict], allow_unverified: bool = False) -> dict`

Reused unchanged from `src/eval/set_b/core.py`: `load_authoring`, `parse_amount`,
`find_classes`, `canonical_bytes`, `sha256_hex`, `UNITS`, `SCALES`.

Not stubbed (implementer, not spec-tested): the Postgres `HxKeyFactStore` (it applies the
fact store's date convention, as set B's `pg_store.py` does, and reads a filing's
comparative columns for `facts_in_filing`), `scripts/eval/hx.py`,
`configs/eval/hx_template.yaml` and a short section in set B's guide.

## Notes

- **Quarantine.** The tests use made-up questions only. Nothing here runs the pipeline or
  calls an LLM; nothing prints a question, answer, passage or ticker. The maintainer keeps
  the authoring file and the built suite outside the repo (as for set B), and the hash
  goes into the v2 pre-registration.
- **Why no tickers in messages.** The plan shares H-X aggregates only. A ticker in an
  error or report line, next to an id, already says part of what a question asks; field
  paths with indices are enough to find the problem in the author's own file.
- **Why honesty and text skip the key check.** Their expectations are statements, judged
  by the claim judge ([eval-judge](eval-judge.md)) and the honesty grading; a
  false-premise `correction` is text here for the same reason.
- **EvalCase kinds.** `EvalCase` accepts `lookup`, `compute`, `dimensional`, `screen`,
  `breakdown`, `narrative`. H-X kinds map onto them (screens to `screen`, scalar
  aggregates and CAGR to `compute`, trends and point-in-time pairs to `breakdown`, text
  and honesty to `narrative`); the H-X kind and sub-type travel in `tags`, and the H-X
  specifics (`universe`, `expected_exclusions`, `accessions`, `key_points`, `passages`,
  `assumptions`, `decline_reason`, `parts`) as extra keys `EvalCase.from_dict` ignores
  today and v2 grading (§3.5) reads.
- **Not here:** set B's P-set rule (it belongs to external test v1), and grading.
