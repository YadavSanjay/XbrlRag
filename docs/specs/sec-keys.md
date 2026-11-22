# sec-keys — answer keys for non-dimensional numbers from SEC companyfacts and frames

v2 evaluation ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.3, §3.8). The v1 suite's
expected values were read from our own materialized views; the SEC check
(`sec-companyfacts-check`) only compared against companyfacts afterwards. This change makes
the SEC's payloads the **source** of the key for every non-dimensional number, with our fact
store as a cross-check. Keys follow the policies in
[Evaluation semantics](../design/XbrlRag_Eval_Semantics.md) and record which ones they used.

This change builds keys and cross-checks them. It does not build suites, fetch over HTTP, or
compute derived ratios (see Notes).

## Requirement

### The I/O seam

1. **`SecSource`** (an `ABC` in `src/eval/keys/base.py`) is where SEC payloads come from:
   - `company_facts(cik: str) -> dict`: the companyfacts JSON of one filer, in the API's
     shape (`{"cik": int, "entityName", "facts": {taxonomy: {tag: {"units": {unit:
     [entry, ...]}}}}}`, each entry `{"start"?, "end", "val", "accn", "fy", "fp", "form",
     "filed", "frame"?}`; an instant has no `start`);
   - `frames(taxonomy: str, tag: str, unit: str, period: str) -> dict`: the frames JSON for
     one concept, unit and calendar period (`{"taxonomy", "tag", "ccp", "uom", "label",
     "description", "pts", "data": [{"accn", "cik", "entityName", "loc", "start"?, "end",
     "val"}, ...]}`).

   An HTTP client is a later change; tests use a fake that reads committed fixture JSON.

### The key record

2. **`AnswerKey`** (a `TypedDict` in `src/eval/keys/core.py`) has exactly these keys:
   - `value: float | None`: the full value as the source gives it, never scaled;
   - `unit: str`: the reporting unit, as the source names it (`USD`, `TWD`, `pure`, ...);
   - `decimals: int | str | None`: the fact's `decimals`. `"INF"` stays the string
     `"INF"`. `None` means **unknown**: the SEC payloads carry no decimals, so SEC keys hold
     `None` until a cross-check supplies it (requirement 9). It is never `0` unless the
     source says `0`;
   - `concept: str`: `"<taxonomy>:<tag>"`, e.g. `"us-gaap:Revenues"`, `"ifrs-full:Revenue"`;
   - `cik: str`: 10 digits, zero-padded;
   - `period_start: str | None` (ISO; `None` for an instant) and `period_end: str`;
   - `fiscal_year: int | None` and `calendar_year: int | None`: the year the key answers,
     whichever applies; the other is `None`;
   - `accession: str`: the accession the value came from;
   - `filed: str | None` and `form: str | None`: of that accession (`None` from frames,
     which carry neither);
   - `key_source: str`: one of `KEY_SOURCES = ("sec_companyfacts", "sec_frames",
     "fact_store", "html_hand")`;
   - `selection: str`: `"latest_filed"`, `"as_reported"` or `"sec_frame"`;
   - `policies: list[str]`: the semantics policies used, in ascending order;
   - `status: str`: `"single-source"` or `"verified"`;
   - `verified_by: list[str]`: the other key sources that agreed (empty when single-source).

   Every builder below returns `status "single-source"` and `verified_by []`.
3. **Errors.** `KeyNotFoundError(LookupError)`: no usable entry. `UnitMismatchError(ValueError)`: the
   concept (or frame) exists but not in the requested unit. `KeyAmbiguousError(ValueError)`: two
   entries tie under the selection rule with different values. A malformed `concept` (no
   `:`) raises `ValueError`.

### Selecting from companyfacts (S1, S6)

4. **Candidate entries** for `(concept, unit, period_start, period_end)`:
   `facts[taxonomy][tag]["units"][unit]`, keeping entries with `end == period_end` and, for
   a duration, `start == period_start`; for an instant (`period_start is None`), entries
   with **no** `start`. Entries whose `val` is `None` (nil) are dropped; a nil is never
   read as 0.
   - concept absent: `KeyNotFoundError`;
   - concept present, `unit` not among its units: `UnitMismatchError`. There is no conversion
     and no unit aliasing (`pure` and `Rate` are different units here);
   - no candidate left: `KeyNotFoundError`.

   The `fy` and `fp` fields are never used to choose among candidates.
5. **`latest_filed_key(company_facts, concept, unit, period_start, period_end)`** (S1): the
   candidate with the greatest `filed`. Several candidates sharing that `filed` date: if
   their values are all equal, the one with the greatest `accn` (string order); otherwise
   `KeyAmbiguousError`. The key has `key_source "sec_companyfacts"`, `selection "latest_filed"`,
   `policies ["S1", "S6"]`, `fiscal_year` and `calendar_year` `None`, and `value`, `accession`,
   `filed`, `form` from that entry. The result does not depend on entry order.
6. **`as_reported_key(company_facts, concept, unit, period_start, period_end,
   accession=None)`** (S1, "as originally reported"):
   - without `accession`: the candidate with the **smallest** `filed`; ties as in 5, but
     resolved to the smallest `accn`;
   - with `accession`: the candidates whose `accn` equals it (equal values: that entry;
     different values: `KeyAmbiguousError`; none: `KeyNotFoundError`).

   `selection "as_reported"`, `policies ["S1", "S6"]`; otherwise as in 5.

### Fiscal and calendar years (S2)

7. **`fiscal_year_key(company_facts, concept, unit, fiscal_year)`**: the year the filer
   itself labels `fiscal_year`.
   1. **Annual entries of year N**: entries of the concept and unit with `form` in
      `{"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}`, `fp == "FY"` and
      `fy == N` (the `fy` field is the filing's fiscal-year focus, so it identifies the
      filings that label year N; it is never used to pick the value). For a duration
      concept only entries lasting 350 to 380 days inclusive (`end - start`) count. A
      concept is a duration concept when any of its entries in the unit has a `start`;
      otherwise only instants (no `start`) count.
   2. **The period**: the greatest `end` among them, which is the filing's current-year
      column (its comparatives end earlier). For a duration, `start` is that entry's
      `start`; two different starts for that end raise `KeyAmbiguousError`. No annual entry of
      year N: `KeyNotFoundError` (even when an entry covering that period exists under another
      `fy`, since the filer's label is then unknown).
   3. **The value**: `latest_filed_key` over that period (S1): a later filing's recast
      comparative wins over the year-N filing's own value.

   The key is that of step 3 with `fiscal_year = N` and `policies ["S1", "S2", "S6"]`.
   Unit and nil handling are those of requirement 4.
8. **`calendar_year_key(frames, cik, unit)`**: frames are calendar-aligned (the SEC picks,
   per filer, the annual period that overlaps the calendar year most, from its latest
   filing), so calendar keys come from frames.
   - `frames["ccp"]` must be `CY####` (a duration year) or `CY####Q4I` (the year-end
     instant); anything else raises `ValueError`. `calendar_year` is the `####`.
   - `frames["uom"] != unit` raises `UnitMismatchError`.
   - The row is the `data` entry whose `int(cik)` equals `int(cik)` (padding ignored); none
     raises `KeyNotFoundError`; a nil `val` raises `KeyNotFoundError`.
   - The key: `concept = f"{taxonomy}:{tag}"`, `cik` padded to 10, `period_start` the row's
     `start` (or `None`), `period_end` its `end`, `accession` its `accn`, `filed` and
     `form` `None`, `decimals None`, `fiscal_year None`, `key_source "sec_frames"`,
     `selection "sec_frame"`, `policies ["S1", "S2", "S6"]`.

### Cross-checking (never overwriting)

9. **`cross_check(key, other, rel_tol=1e-9) -> CrossCheck`**, where `CrossCheck` is a
   `TypedDict` `{"key": AnswerKey, "disagreement": Disagreement | None}`. `other` is a key
   from another source, typically the fact store's value as an `AnswerKey` with
   `key_source "fact_store"`.
   - A `key_source` not in `KEY_SOURCES`, or the same `key_source` on both sides, raises
     `ValueError` (not independent).
   - The first failing test, in this order, is the disagreement `reason`: `"nil"` (either
     value is `None`), `"concept"`, `"unit"`, `"period"` (`period_start` or `period_end`
     differ), `"value"` (`abs(a - b) > rel_tol * max(abs(a), abs(b))`).
   - **Agreement**: `disagreement` is `None`, and `key` is a copy of the input key with
     `status "verified"`, `other["key_source"]` appended to `verified_by` (once), and
     `decimals` taken from `other` when the input key's is `None` (`"INF"` stays `"INF"`).
     `value`, `accession`, `key_source` and the rest are the input key's.
   - **Disagreement**: `key` equals the input key unchanged (its `value` is never replaced
     by `other`'s), and `disagreement` is a `Disagreement` (`TypedDict`) with: `reason`,
     `concept`, `unit`, `period_start`, `period_end` (all from `key`), `key_source`,
     `key_value`, `key_accession`, `other_source`, `other_value`, `other_unit`,
     `other_accession`.
   - The inputs are not mutated.

### Through the seam

10. **`fiscal_key_from_source(source, cik, concept, unit, fiscal_year)`** calls
    `source.company_facts(cik)` once and returns `fiscal_year_key` over it.
    **`calendar_key_from_source(source, cik, concept, unit, calendar_year, instant=False)`**
    calls `source.frames(taxonomy, tag, unit, period)` once, with `period = f"CY{year}"`,
    or `f"CY{year}Q4I"` when `instant`, and returns `calendar_year_key(frames, cik, unit)`.

## Acceptance criteria

Fakes sit only at the seam: a `FakeSecSource` reading the committed fixture JSON under
`test/spec/sec-keys/fixtures/`. All payloads are synthetic, in the SEC APIs' shape, with
values chosen by hand; every expected value is read off the fixture in the test. Values are
compared exactly or with `pytest.approx(rel=1e-12)`: keys copy the source's numbers, so any
difference is a bug, not rounding.

- **C1**: `latest_filed_key` (S1). On the September filer: FY2022 revenue is the FY2024
  10-K's recast `321_500_000_000` (accession `0000900001-24-000030`, filed `2024-11-01`),
  not the FY2022 10-K's `320_000_000_000`; the key's fields are those of requirement 5;
  `decimals` is `None`. An instant (`Assets`) matches only entries without `start`, and a
  duration period queried as an instant is `KeyNotFoundError`. A nil latest entry is skipped
  (the earlier non-nil value wins); all-nil is `KeyNotFoundError`. Same-day equal values pick
  the greater accession; same-day different values raise `KeyAmbiguousError`. Metamorphic:
  shuffling the entries, or adding entries filed earlier with any value, leaves the key
  unchanged. Oracle: requirements 4–5, the fixture.
- **C2**: `as_reported_key` (S1). FY2022 revenue as originally reported is
  `320_000_000_000` from `0000900001-22-000010`; with `accession` the FY2024 10-K it is
  the recast value; an accession that does not report the period is `KeyNotFoundError`.
  Property: for every accession reporting a period, `as_reported_key(..., accession=a)`
  returns that accession's value and `selection "as_reported"`. Oracle: requirement 6.
- **C3**: `fiscal_year_key` (S2). September filer FY2023 resolves to `2022-09-25` →
  `2023-09-30` (a 53-week year) with the FY2024 10-K's recast `340_750_000_000`; FY2022
  ignores the 3-month `fp=FY` entry; an instant concept resolves to its year-end balance;
  a June filer's FY2024 ends `2024-06-30`; a 20-F filer's TWD FY2024 is accepted. A year
  with only a 10-Q (`fy=2025`) and a year with no filing labelled it (`fy=2021`) are
  `KeyNotFoundError`. Metamorphic: appending a later 10-K that recasts year N to any value `v`
  changes the key's value to `v` and leaves its period unchanged. Oracle: requirement 7,
  the fixture.
- **C4**: `calendar_year_key` (S2). From the CY2024 revenue frame, the June filer's key is
  its fiscal 2025 period (`2024-07-01` → `2025-06-30`, `281_724_000_000`), which differs
  from its `fiscal_year_key(2024)`; the September filer's is its FY2024; the instant frame
  `CY2024Q4I` gives a key with `period_start None`. A CIK absent from the frame is
  `KeyNotFoundError`; a bad `ccp` is `ValueError`. Property: over generated frames with
  distinct CIKs, each CIK's key holds exactly its row's value, period and accession.
  Oracle: requirement 8.
- **C5**: units and scale (S6). Keys hold the source's full value (`2_894_307_699_000`
  TWD, not scaled) in the source's unit. Requesting `USD` for a TWD-only concept, `Rate`
  for a `pure` concept, or `EUR` from a USD frame raises `UnitMismatchError` in each builder.
  Property: any unit string the concept lacks raises `UnitMismatchError`, never returns a key.
  Oracle: requirement 4 and 8, S6.
- **C6**: `cross_check`. A fact-store key equal to the SEC key gives `verified`,
  `verified_by ["fact_store"]`, and adopts `decimals` `-6` or `"INF"` (kept as `"INF"`).
  A different value gives a disagreement with `reason "value"` and both values, and the
  returned key keeps the SEC value and `single-source`. Each other reason (`nil`,
  `concept`, `unit`, `period`) is produced; a nil is not compared as 0. Same or unknown
  `key_source` raises `ValueError`. Inputs are not mutated. Property: for a relative
  perturbation `d`, `|d| <= rel_tol / 2` verifies and `|d| >= 2 * rel_tol` disagrees, for
  `rel_tol` in `{1e-9, 5e-3}`. Oracle: requirement 9.
- **C7**: through the seam. `fiscal_key_from_source` calls `company_facts` once with the
  CIK and equals `fiscal_year_key` on the same fixture's expected values;
  `calendar_key_from_source` calls `frames("us-gaap", "Revenues", "USD", "CY2024")`, or
  `"CY2024Q4I"` with `instant=True`, once. Property: for any year, the period string is
  `CY{year}` / `CY{year}Q4I`. Oracle: requirement 10.

## Interface

New, stubbed in the spec PR (stub `TypedDict`s list their keys in their docstrings; the
implementer adds the annotations):

`src/eval/keys/__init__.py`: comments only.

`src/eval/keys/base.py`:
- `class SecSource(ABC)` with `@abstractmethod company_facts(self, cik: str) -> dict` and
  `@abstractmethod frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict`.

`src/eval/keys/core.py`:
- `KEY_SOURCES = ("sec_companyfacts", "sec_frames", "fact_store", "html_hand")` (added by
  the implementer: the stub lint admits no module-level assignments; tests don't import it);
- `class KeyNotFoundError(LookupError)`, `class UnitMismatchError(ValueError)`,
  `class KeyAmbiguousError(ValueError)`;
- `class AnswerKey(TypedDict)`, `class Disagreement(TypedDict)`, `class CrossCheck(TypedDict)`;
- `latest_filed_key(company_facts: dict, concept: str, unit: str, period_start: str | None, period_end: str) -> AnswerKey`;
- `as_reported_key(company_facts: dict, concept: str, unit: str, period_start: str | None, period_end: str, accession: str | None = None) -> AnswerKey`;
- `fiscal_year_key(company_facts: dict, concept: str, unit: str, fiscal_year: int) -> AnswerKey`;
- `calendar_year_key(frames: dict, cik: str, unit: str) -> AnswerKey`;
- `cross_check(key: AnswerKey, other: AnswerKey, rel_tol: float = 1e-9) -> CrossCheck`;
- `fiscal_key_from_source(source: SecSource, cik: str, concept: str, unit: str, fiscal_year: int) -> AnswerKey`;
- `calendar_key_from_source(source: SecSource, cik: str, concept: str, unit: str, calendar_year: int, instant: bool = False) -> AnswerKey`.

Not in this change: the HTTP `SecSource` (it can reuse `SecCompanyFactsClient`'s pacing and
cache from `src/eval/sec_check/sec_client.py`, plus the frames URL), a Postgres reader that
turns `mv_financial_facts` rows into `fact_store` keys, and the suite builder that calls
these.

## Notes

- **Derived ratios (S4) are out of scope.** A later spec computes them in the test layer
  from keys built here (S1 applied to each input), recording the definition id and the
  average-or-closing choice; it never reads `mv_derived_metrics` or the calculator.
- **Dimensional, breakdown and untagged numbers** are not in companyfacts or frames; their
  keys are `html_hand` (plan §3.3) and a later change.
- **Payload shapes**: SEC "EDGAR Application Programming Interfaces" (companyfacts and
  frames, `data.sec.gov/api/xbrl/...`). Neither payload carries `decimals`, hence `None`
  on SEC keys until a fact-store cross-check supplies it. Frames' "latest filing" dedupe is
  the SEC's, which is why frames keys also cite S1.
- **Why `fy` is not the period.** companyfacts' `fy` is the filing's fiscal-year focus; a
  10-K's comparatives carry the same `fy` as its current year. Picking "fy = 2023, latest
  end" from the FY2023 10-K returns the original FY2023 value; S1 wants the FY2024 10-K's
  recast. The fixture's September filer is built to show both traps.
- **The 350–380 day window** admits 52/53-week years (364/371 days) and excludes the
  quarterly durations some 10-Ks tag with `fp=FY`.
- **Tolerance.** `rel_tol` is a parameter because a hand-keyed HTML value is rounded to the
  filing's presentation scale; the default `1e-9` is for SEC-vs-fact-store, which read the
  same XBRL value.
- **Fixtures** (synthetic; CIKs `0000900001`–`0000900004` are made up): a September
  52/53-week filer with three 10-Ks, a 10-K/A and a 10-Q; a June filer; a December 20-F
  filer reporting in TWD; a CY2024 revenue frame and a CY2024Q4I assets frame.
- Cost: $0; no network.
