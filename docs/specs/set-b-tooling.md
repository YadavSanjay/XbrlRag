# set-b-tooling — author, check, build and seal the hand-written held-out set B

External test v1. Set B is 40–60 analyst questions **written by the user**, sealed
before the freeze and not run until the test. It isolates **question wording**: same filers
and years the system was developed on, but phrasing nobody tuned for.

B only proves something if the system's author (Claude) never sees the questions, and if
the system never runs on them before the seal. This change gives the user a way to write
B and to confirm its answer keys **with SQL only**, with no pipeline and no LLM, and turn
it into an `EvalCase` suite the existing graders can run. No output of the tooling ever
contains a question's text.

Decisions (2026-10-22):
- **Filers:** the 12 suite filers (AAPL, AMD, AMZN, AVGO, GOOGL, META, MSFT, MU, NVDA,
  ORCL, TSLA, TSM).
- **Years:** fiscal years 2024–2026.
- **Excluded:** every (filer, FY2026) pair in the prospective set P. Today that's AAPL
  FY2026 (pre-registration v0 §3).
- **Format:** YAML.

## Requirement

### The authoring file

1. **Shape.** The file is YAML with a top-level `cases:` list. Each case has:
   - `id` (str), `question` (str);
   - `kind`: one of `lookup`, `dimensional`, `compute`, `breakdown`;
   - `ticker` (str), `fy` (int);
   - for `lookup` and `dimensional`: `expected`, plus an optional `scale` and a `unit`
     (one of `USD`, `TWD`, `pure`, `shares`, `USD/shares`);
     - an optional `concept` hint (str);
     - for `dimensional`, a required `member` hint (str);
   - for `compute`:
     - `formula`: one of `yoy`, `ratio`, `difference`, `sum`;
     - `expected`, plus an optional `scale`;
     - `inputs`: a list of `{name, expected, scale?, fy?, unit, concept?, member?}`.
       `fy` defaults to the case's `fy`;
   - for `breakdown`: `unit`, and `rows`: a list of `{member, expected, scale?}`;
   - an optional `note` (str, never printed).

   `load_authoring(text)` parses the YAML with `yaml.safe_load` and returns the list of
   case dicts. A missing or non-list `cases` raises `ValueError`.
2. **Amounts.** `parse_amount(raw, scale)` turns an amount as written into `(value,
   half_unit)`, where `half_unit` is half of the last written digit.
   - `raw` is a number or a string.
     - A string may carry thousands separators (`,`), a leading `-`, wrapping
       parentheses (meaning negative), a leading `$`, and a trailing `%`.
     - Whitespace is ignored.
   - `scale` is one of `None`/`units` (×1), `thousands` (×1e3), `millions` (×1e6) or
     `billions` (×1e9).
   - **Without `%`:** `value = number × scale`, and `half_unit = 0.5 × scale ×
     10**-d`, where `d` is the count of digits written after the decimal point.
   - **With `%`:** `value = number / 100`, `half_unit = 0.5 × 10**-d / 100`, and `scale`
     must be `None`.
   - A number given as a YAML int or float counts its decimals from `str(raw)`.
   - Anything else raises `ValueError`.
   - Examples:
     - `"391,035"` in millions gives `(391035e6, 0.5e6)`;
     - `"(1.25)"` in billions gives `(-1.25e9, 0.005e9)`;
     - `"21.5%"` gives `(0.215, 0.0005)`.
3. **Agree.** A fact value `v` agrees with a written amount `(value, half_unit)` when
   `abs(v - value) <= max(half_unit, 1e-9 * abs(value))`.

### Validation (`validate`)

4. **`validate(cases)` returns `{"errors": [...], "warnings": [...], "mix": {...}}`.**
   - Every **error** is a string that starts with the case's `id` (or `#<index>` when the
     id is missing) and names the field.
   - **Warnings** describe the whole set, so they carry no id prefix.
   - A compute **input's** `fy` may be 2022–2026, so a FY2024 YoY can read FY2023. A case's
     own `fy` must be 2024–2026.
   - **Errors:**
     - a missing required field, or a wrong type;
     - an unknown `kind`, `formula`, `unit` or `scale`;
     - a duplicate `id`;
     - a ticker outside the 12 suite filers;
     - `fy` outside 2024–2026;
     - a (ticker, fy) in the P set, i.e. `("AAPL", 2026)`. This also applies to any
       input `fy` of a compute case;
     - an amount `parse_amount` rejects;
     - a `yoy` without exactly 2 inputs named `current` and `prior`;
     - a `ratio` without inputs `numerator` and `denominator`;
     - a `difference` without `a` and `b`;
     - a `sum` with fewer than 2 inputs;
     - a `breakdown` with fewer than 2 rows;
     - a `dimensional` case without `member`.
   - **Warnings:**
     - a total case count outside 40–60;
     - any kind with 0 cases;
     - more than 40% of cases on one ticker.
   - **`mix`:** `{"kind": {kind: n}, "ticker": {ticker: n}, "fy": {fy: n}, "total": n}`,
     with int `fy` keys. A value with no cases may be absent.
   - No message contains a case's `question` or `note`.

### Checking answer keys (`resolve_case`)

5. **Seam.** `KeyFactStore.facts_for(ticker, fiscal_year)` (in
   `src/eval/set_b/base.py`) returns the candidate facts for one filer-year: annual
   (FY) durations and FY-end instants, flat and dimensional. Each candidate is a dict
   with these keys:
   - `fact_id`, `concept` (namespace URI, `:`, local name), `concept_family` (str or
     None), `unit`, `value` (float);
   - `period_start` (str or None), `period_end` (str);
   - `dimensional` (bool);
   - `member_label`: the member label the main suite's breakdown template uses for the
     `segment_name` column, or None for a flat fact.
6. **Finding one amount.** `find_classes(candidates, amount, unit, dimensional, concept,
   member)` returns the matching facts grouped into **classes**.
   - A candidate matches when **all** of these hold:
     - its `dimensional` equals `dimensional`;
     - its `unit` equals `unit`;
     - its value agrees with `amount` (requirement 3);
     - when `concept` is given: it equals `concept_family`, or it is a case-insensitive
       substring of the local name;
     - when `member` is given: it is a case-insensitive substring of `member_label`.
   - A class is the matches that share `(concept, unit, period_start, period_end,
     member_label)`.
   - The result is a list of classes. Each class is the sorted list of its fact ids, and
     the classes are sorted by their first id.
7. **`resolve_case(case, store)` returns** `{"id", "status", "inputs": [...], "classes":
   [...], "candidates_hint": [...]}`.
   - **`lookup` and `dimensional`:** one `find_classes` call over `facts_for(ticker,
     fy)`, with `dimensional` set by the kind.
     - `status` is `verified` with exactly 1 class, `ambiguous` with more than 1, and
       `not_found` with 0.
     - `classes` holds the classes.
     - `candidates_hint` lists, for an ambiguous case, each class's `concept` local name
       and `member_label`, never values from the question.
   - **`compute`:** each input is resolved like a lookup on `facts_for(ticker, input
     fy)`. It is dimensional exactly when it has `member`.
     - The case's `inputs` list holds each input's `{name, status, classes}`.
     - `recompute(formula, values)` runs over the inputs' written values:
       - `yoy`: `(current - prior) / abs(prior)`;
       - `ratio`: `numerator / denominator`;
       - `difference`: `a - b`;
       - `sum`: the sum of all inputs;
       - a zero denominator gives `None`.
     - `status`, decided in this order:
       1. the first non-`verified` input status, if any;
       2. `arith_mismatch` when the recomputed value is `None`, or doesn't satisfy
          `abs(r - expected) <= max(expected_half_unit, 1e-3 * abs(expected))`;
       3. `verified` otherwise.
     - `classes` is one class per input, in input order.
   - **`breakdown`:** each row is resolved as a dimensional amount with
     `member=row["member"]`.
     - The case's `inputs` list holds each row's `{name: member, status, classes}`.
     - `status` is the first non-`verified` row status, or `verified`.
     - `classes` is one class per row.
   - `facts_for` is called at most once per (ticker, fiscal year) within one
     `resolve_case`.
8. **The report.** `render_check_report(resolutions)` returns a text report: one line
   per case (`id`, `status`, the number of classes), then a count per status. For a
   non-verified case, it adds the input name and the `candidates_hint`. It never contains
   a question, a note or an expected amount.

### Building and sealing

9. **`build_suite(cases, resolutions, allow_unverified=False)` returns the suite dict**
   `{"name": "heldout_b", "version": "v1", "n_cases": n, "cases": [...]}`.
   - If any case isn't `verified`, it raises `ValueError` listing their ids. With
     `allow_unverified`, those cases are left out, and their **authoring** ids (without
     the `b-` prefix) go in `"excluded": [...]`.
   - A breakdown case carries no `unit` and no `expected_value`, as in the main suite.
   - Cases are sorted by `id`. Each is an `EvalCase` dict with:
     - `id`: the authoring id, prefixed `b-`;
     - `question`, `kind`, `ticker`, `fy`;
     - `suite: "heldout_b_v1"`;
     - `origin: "handwritten:set_b"`;
     - `tags: ["set-b", kind]`.
   - **`lookup` and `dimensional`:**
     - `expected_value` is the parsed amount;
     - `unit`;
     - `expected_fact_ids` is `[class[0]]`, and `fact_id_classes` is `[class]`;
     - `rel_tol` is `1e-4`.
   - **`compute`:**
     - `expected_value`;
     - `alt_values` is `[expected_value * 100]` when `expected` was written with `%`,
       else `[]`;
     - `expected_fact_ids` holds each input class's first id, and `fact_id_classes`
       holds the input classes;
     - `rel_tol` is `1e-2`;
     - `unit` is the first input's unit.
   - **`breakdown`:**
     - `expected_rows` is `[{"entity": <member_label of the row's class>, "value":
       <parsed amount>}]` in authoring order;
     - `order_sensitive: false`;
     - `expected_fact_ids` and `fact_id_classes` per row;
     - `rel_tol` is `1e-4`.

     The `member_label` is read from the store, so `build_suite` also takes a `labels`
     map of `{fact_id: member_label}`. Its signature is `build_suite(cases,
     resolutions, labels, allow_unverified=False)`.
   - `question` is copied verbatim. `note` is never copied.
10. **Canonical bytes and the hash.**
    - `canonical_bytes(suite)` is `json.dumps(suite, sort_keys=True, indent=2,
      ensure_ascii=False) + "\n"`, encoded as UTF-8.
    - `sha256_hex(data)` is the lowercase SHA-256 hex digest.
    - The same suite always gives the same bytes.
11. **The CLI** `scripts/eval/set_b.py` has five subcommands:
    - `validate FILE`;
    - `check-keys FILE` (needs the database);
    - `build FILE --out OUT [--allow-unverified]`;
    - `seal OUT`, which prints `<sha256>  <basename>`;
    - `verify OUT HASH`, which exits non-zero on a mismatch.

    Errors and reports go to stdout with no question text. The CLI never imports the
    retrieval pipeline or any LLM module. A Postgres `KeyFactStore` (read-only) backs
    `check-keys` and `build`.

    The authoring template is `configs/eval/set_b_template.yaml`. It holds one
    commented, obviously fictional example per kind (ticker `EXAMPLE`, which fails
    validation on purpose). A one-page guide sits in `docs/guides/xbrl-flow/`.

    The Postgres store, the CLI and the template aren't covered by spec tests.

## Acceptance criteria

Fakes sit only at the I/O seam: a fake `KeyFactStore` returning synthetic candidates and
recording its calls. YAML text is built in the tests. All values are synthetic and chosen
by hand.

- **C1**: `load_authoring` and `parse_amount`.
  - The three examples in requirement 2.
  - A YAML int and a YAML float.
  - `thousands` and `units` scales.
  - Errors: a `%` with a scale, an unknown scale, the text `"abc"`, and a missing or
    non-list `cases`.

  Property: for random digit strings, decimals 0–3 and scales,
  `parse_amount(format(x))` returns `x × scale` (within float precision) and the
  `half_unit` formula. Oracle: requirements 1–3.
- **C2**: `validate`.
  - Each error listed in requirement 4 is raised by a minimal bad case. The message
    starts with the id.
  - `("AAPL", 2026)` is rejected for a lookup and as a compute input `fy`, while
    `("AAPL", 2025)` is accepted.
  - The three warnings, and the `mix` counts.
  - No message contains the question or note text.

  Property: over random valid cases, `errors` is empty and `mix.total` equals the case
  count. Oracle: requirement 4 and pre-registration v0 §3.
- **C3**: `find_classes`.
  - A value that agrees within `half_unit` matches; one just outside doesn't.
  - Unit, dimensional, `concept` (family or local-name substring) and `member` filters
    each exclude what they should.
  - Two facts with the same concept, period and member form one class. Two different
    concepts form two classes.
  - Classes and their ids are sorted.

  Property: adding candidates with a different unit never changes the result. Oracle:
  requirements 3 and 6.
- **C4**: `resolve_case` for lookup and dimensional.
  - `verified`, `ambiguous` (with a `candidates_hint` that has no question text or
    amount) and `not_found`.
  - A dimensional case uses only dimensional candidates, filtered by `member`.
  - `facts_for` is called once.

  Metamorphic: shuffling the candidate list doesn't change the result. Oracle:
  requirement 7.
- **C5**: `resolve_case` for compute and breakdown, and `recompute`.
  - `yoy`, `ratio`, `difference` and `sum`, with the arithmetic shown in the test.
  - An input at `fy - 1` reads that year's facts.
  - Arithmetic that is off gives `arith_mismatch`.
  - An unresolved input's status is passed up.
  - A zero denominator gives `None`, then `arith_mismatch`.
  - A breakdown resolves every row, and passes up the first bad row's status.
  - `facts_for` is called at most once per (ticker, fy).

  Property: for random positive inputs, `recompute("ratio")` times the denominator
  returns the numerator. Oracle: requirement 7.
- **C6**: `build_suite`, `canonical_bytes` and `sha256_hex`.
  - The exact `EvalCase` dict for one case of each kind, built by hand in the test.
  - An unverified case raises. With `allow_unverified`, it's left out and listed.
  - Cases are sorted by id, and `note` isn't copied.
  - `sha256_hex(b"abc")` is the published digest
    `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad` (FIPS 180-2
    example).

  Property: building twice from the same inputs, with the case list shuffled, gives
  identical bytes. Oracle: requirements 9 and 10.
- **C7**: `render_check_report`.
  - One line per case, plus status counts.
  - For a mixed set of resolutions whose questions, notes and amounts are distinctive
    strings, none of those strings appears in the report.

  Property: over random question strings, no question appears in the report. Oracle:
  requirement 8.

## Interface

New, stubbed in the spec PR. New files carry no module docstring, because the stub lint
allows none. `TypedDict`s aren't used: everything here is a plain `dict`.

`src/eval/set_b/__init__.py`: comments only.

`src/eval/set_b/base.py`:
- `class KeyFactStore(ABC)`, with
  `@abstractmethod facts_for(self, ticker: str, fiscal_year: int) -> list[dict]`.

`src/eval/set_b/core.py`:
- `load_authoring(text: str) -> list[dict]`
- `parse_amount(raw: object, scale: str | None = None) -> tuple[float, float]`
- `validate(cases: list[dict]) -> dict`
- `find_classes(candidates: list[dict], amount: tuple[float, float], unit: str, dimensional: bool, concept: str | None = None, member: str | None = None) -> list[list[int]]`
- `recompute(formula: str, values: dict[str, float]) -> float | None`
- `resolve_case(case: dict, store: KeyFactStore) -> dict`
- `render_check_report(resolutions: list[dict]) -> str`
- `build_suite(cases: list[dict], resolutions: list[dict], labels: dict[int, str], allow_unverified: bool = False) -> dict`
- `canonical_bytes(suite: dict) -> bytes`
- `sha256_hex(data: bytes) -> str`

Not stubbed (implementer, not spec-tested):
- `src/eval/set_b/pg_store.py`. It has to apply the fact store's date convention, as
  `sec_check/pg_store.py` does (stored ends are one day late).
- `scripts/eval/set_b.py`, `configs/eval/set_b_template.yaml`, and the guide.

## Notes

- **The quarantine rules of the external test apply to the tooling itself:**
  - nothing here runs the retrieval pipeline or calls an LLM;
  - nothing prints a question;
  - the user keeps the authoring file and the built suite **outside the repo**, for
    example under `<vault>\set_b\`, until the test run unseals it.
- **The hash goes into pre-registration v1.** The built file is encrypted into the vault
  (decision 5 in the plan, still open).
- **The answer keys are confirmed against our own fact store.** For the non-dimensional
  cases, the SEC check (`scripts/eval/check_against_sec.py --suite <built file>`)
  can also confirm them against the SEC. That's optional, and run by the user so the
  report stays theirs.
