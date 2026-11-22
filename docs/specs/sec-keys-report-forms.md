# sec-keys-report-forms — answer keys read only annual and quarterly reports

A trial build of the v2 suites (`data/eval/v2_suites_trial/`, built 2026-10-29) showed that
the SEC key builders in `src/eval/keys/core.py` choose among companyfacts entries of **any**
form. 26 of 120 cases rested on non-report forms: DEF 14A proxy statements (for example a
filer's FY2023–25 `NetIncomeLoss` taken from a pay-versus-performance table in a proxy filed
2026-10-19), 8-Ks (a FY2022 capex figure from an earnings release) and a PRE 14C.

[Evaluation semantics](../design/XbrlRag_Eval_Semantics.md) S1 says a period's value comes
from "the latest filing **in the corpus** that reports it", and the corpus is annual and
quarterly reports: [`VISION.md`](../../VISION.md) lists proxy statements and 8-K earnings
releases as non-goals. A key taken from a proxy or an 8-K is therefore a key from outside
the corpus, and a later proxy that restates a number (often rounded, sometimes on a different
basis) silently replaces the 10-K's value.

This change narrows what every companyfacts key builder reads. It does not change the
selection rules of [sec-keys](sec-keys.md) (S1 latest filed, as reported, S2 fiscal-year
discovery) or how keys are recorded.

## Requirement

1. **Report forms.** `REPORT_FORMS` (a public module-level `frozenset[str]` in
   `src/eval/keys/core.py`) is exactly
   `{"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-Q", "10-Q/A"}`.
   `is_report_form(form)` returns `True` exactly when `form` is a `str` equal to one of them
   (case-sensitive, no stripping: companyfacts writes form types in this canonical
   spelling, as with units in sec-keys requirement 4); `None`, a missing form and every
   other string are `False`. The set is defined once; the annual subset used for fiscal-year
   discovery (sec-keys requirement 7.1, `_ANNUAL_FORMS` in `core.py` and in
   `src/eval/suites/v2_suite.py`) stays as it is and is a subset of it.
2. **As if absent.** Every companyfacts key builder behaves exactly as it would on the same
   payload with every entry whose `form` is not a report form removed (an entry without a
   `form` field counts as not a report form). After that removal, a unit with no entries
   left is absent from the concept, and a concept with no units left is absent. So:
   - a period reported only in non-report forms has no candidate: `KeyNotFoundError`, never
     the non-report value;
   - a concept reported only in non-report forms is absent: `KeyNotFoundError`;
   - a unit present only in non-report entries is absent: `UnitMismatchError` when the
     concept has report entries in another unit;
   - a non-report entry never wins S1 (even when filed later), never becomes the "as
     originally reported" value (even when filed earlier, like an earnings-release 8-K that
     precedes its 10-K), never creates a same-day `KeyAmbiguousError`, and is never matched by
     `as_reported_key(..., accession=...)`: an accession whose entries are all non-report is
     `KeyNotFoundError`;
   - in `fiscal_year_key`, whether a concept is a duration concept (sec-keys requirement
     7.1, "any entry in the unit has a `start`") is decided over report entries only.

   The builders are `latest_filed_key`, `as_reported_key`, `fiscal_year_key`, and everything
   built on them: `fiscal_key_from_source`, `src/eval/keys/sec_live.py`
   `fiscal_key_with_payload`, and the v2 suite keyer (`src/eval/suites/v2_suite.py`
   `key_intent`, `build_v2_suites`). The filter applies once, in `core.py`; the callers need
   no change of their own beyond inheriting it.
3. **Consequences for the v2 suite.** An intent whose only value for its period comes from a
   non-report form is dropped with reason `"no_key"` (sec-keys errors map to v2-suite-build
   drop reasons unchanged). A comparison's "one filing" (S1) is the latest **report** that
   reports both years, so `same_filing` never names a proxy or an 8-K.
4. **Frames are unchanged.** `calendar_year_key` and `calendar_key_from_source` /
   `calendar_key_with_payload` keep their sec-keys behaviour (see Notes for why, and for the
   gap this leaves).
5. Nothing else changes: the key record, the selection and tie rules of sec-keys
   requirements 4–7, and every retired sec-keys and v2-suite-build test (their fixtures use
   only report forms).

## Acceptance criteria

All payloads are synthetic, in the companyfacts shape, built by hand in
`test/spec/sec-keys-report-forms/conftest.py` (one December filer, CIK `0000900201`, with
10-Ks, a 10-Q, a 10-KT, 8-Ks, DEF 14A proxies and a PRE 14C). Values are compared exactly:
keys copy the source's numbers, so any difference is a bug, not rounding.

- **C1**: `is_report_form` is `True` for each of the eight report forms and `False` for
  `10-KT`, `10-QT`, `8-K`, `8-K/A`, `DEF 14A`, `DEFA14A`, `PRE 14C`, `6-K`, `S-1`, `10-k`,
  `" 10-K"`, `""` and `None`. Property: for any string or `None`, the result equals
  membership in the eight literal forms. Oracle: requirement 1.
- **C2**: `latest_filed_key` reads report forms only. FY2023 net income is the FY2024
  10-K's recast `14_974_000_000`, not the later proxies' `14_997_000_000` / `14_999_000_000`;
  FY2024 is `7_091_000_000` from the 10-K, not the 2026 proxy, the PRE 14C or a form-less
  entry; FY2024 capex is the 10-K's, not a later 8-K's; FY2024 revenue is the 10-K's
  `97_690_000_000` although an 8-K filed the same day says `97_700_000_000` (no
  `KeyAmbiguousError`); FY2024 operating income ignores a later 10-KT; FY2021 net income
  (proxy only) and a concept reported only in proxies are `KeyNotFoundError`; revenue in
  `EUR` (8-K only) is `UnitMismatchError`; a 10-Q's quarter is still keyed. Property: adding
  any number of non-report entries, of any form, period, value, filing date, concept and
  unit, leaves every probe's outcome (the key, or the error class) unchanged. Oracle:
  requirement 2, the fixture.
- **C3**: `as_reported_key` reads report forms only. FY2024 capex as originally reported is
  the 10-K's `11_339_000_000`, not the earnings-release 8-K filed a week earlier;
  `accession=` that 8-K, or a proxy, is `KeyNotFoundError`; `accession=` the 10-K still works.
  Property: as in C2, for `as_reported_key` with and without `accession`. Oracle:
  requirement 2.
- **C4**: `fiscal_year_key` reads report forms only. FY2023 and FY2024 net income are the
  FY2024 10-K's values with the 10-K's accession and `form "10-K"`; FY2024 revenue is the
  10-K's (no ambiguity); FY2024 equity is the 10-K's year-end instant although an 8-K tags
  the concept as a duration; FY2023 `ProfitLoss`, nil in the 10-K and present only in a
  proxy, is `KeyNotFoundError`. Property: as in C2, for `fiscal_year_key` over years and
  concepts. Oracle: requirement 2, sec-keys requirement 7.
- **C5**: the callers inherit it. `fiscal_key_from_source` and `fiscal_key_with_payload`
  (a `LiveSecSource` over an in-memory store and a fake transport) return the 10-K's FY2024
  net income. `key_intent`: a FY2023 net-income lookup is the 10-K's recast; a FY2023
  `ProfitLoss` lookup is dropped `"no_key"`; a FY2023→FY2024 growth is computed from the
  FY2024 10-K (`same_filing True`, both inputs its accession, value
  `(7_091 - 14_974) / 14_974`). Property: adding non-report entries does not change a
  keyed lookup. Oracle: requirements 2–3.

## Interface

New, stubbed in the spec PR:

`src/eval/keys/core.py`:
- `is_report_form(form: str | None) -> bool`;
- `REPORT_FORMS: frozenset[str]` (added by the implementer: the stub lint admits no
  module-level assignments; tests reach it only through `is_report_form`).

Behaviour changes, written as requirements on the implementer (no signature changes):
`latest_filed_key`, `as_reported_key`, `fiscal_year_key` in `core.py`, and through them
`fiscal_key_from_source`, `sec_live.fiscal_key_with_payload` and `v2_suite.key_intent`.

## Notes

- **Why 10-KT is not a report form.** A 10-KT (and 10-QT) is a transition report filed when
  a filer changes its fiscal year-end. The corpus downloads only `10-K`, `10-Q` and `20-F`
  (`configs/pipelines/sec_edgar_downloads.yaml`), so a 10-KT is never in it, and its
  transition period is not a fiscal year the S2 rules can label. Admitting it would let a key
  come from a filing the system never ingested, which is the bug this change fixes. If the
  corpus starts ingesting transition reports, adding them to `REPORT_FORMS` is a spec change
  (the suites are rebuilt and diffed, as for any S1 change). 40-F and the amendments are kept
  because sec-keys already treats them as annual reports and the corpus may hold them.
- **Why frames are not filtered here.** A frames row carries `accn` but no `form`, and an
  accession number's prefix is the CIK of whoever submitted the filing (often a filing
  agent), not a form code, so the form cannot be recovered from a frames payload alone. The
  SEC builds each frame from the filer's latest-filed fact for the period, which can be a
  proxy or an 8-K, so frames keys share the bug in principle. No suite uses calendar keys
  today (`v2_suite` imports only `fiscal_year_key` and `as_reported_key`), so this change
  leaves them alone. Before any suite uses a frames key, a later change must resolve the
  row's `accn` against the filer's companyfacts (where every entry has `accn` and `form`) and
  reject a row whose accession is not a report.
- **Missing form.** Real companyfacts entries always carry `form`; an entry without one
  cannot be shown to be a corpus filing, so it is ignored like a proxy.
- **Retired tests.** sec-keys and v2-suite-build fixtures use only `10-K`, `10-K/A`, `20-F`
  and `10-Q` entries, so their locked tests stay valid unchanged. The unit test
  `test/unit/test_eval_keys.py::test_fiscal_year_ignores_non_annual_forms` stays valid.
- Cost: $0; no network, DB or LLM.
