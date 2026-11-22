# XbrlRag blog v1: pre-registration v1, amendment 2

**Date:** 2026-10-28.

**Status:** sealed by the merge of this file into `main`, and by an OpenTimestamps proof
over the hashes in [`HASHES_v1_amendment_2.txt`](HASHES_v1_amendment_2.txt).

This is the second numbered amendment to [pre-registration v1](preregistration_v1.md),
after [amendment 1](preregistration_v1_amendment_1.md). It changes when and where set B is
run, and the audit item that this affects. Nothing else in v1 or amendment 1 changes.

## 1. Set B runs on `xbrl_db`, on 2026-10-28

**Change.** v1 §6 runs the held-out sets A′ and B on one day, after the §7 audit. Instead:

- **B's runs** (H1 and H2 on B, and H3) are made on **2026-10-28**, at the tag
  `blog-v1-freeze`, against the main environment: Postgres `xbrl_db`, its Qdrant, and the
  main filing roots. They are made after the §7 audit (section 3 below), and only once
  this amendment's proof is confirmed in a Bitcoin block. If it is not confirmed on
  2026-10-28, this change lapses and B runs with A′ as v1 states.
- **A′'s runs** (H1 and H2 on A′) are made on their own day, in the held-out environment,
  after A′'s ingest and suite build and after the §7 audit is repeated.
- Each held-out set is still run in full on one day: B's three systems together, A′'s two
  systems together.
- H3's condition (v1 §6) is checked against the spend recorded before B's runs. It is
  about $70 at the time of writing, so H3 runs.

**Reason.**

- **B's answer keys belong to `xbrl_db`.** `scripts/eval/set_b.py check-keys` confirmed
  every key against `xbrl_db`, whose corpus fingerprint is still the freeze's,
  `pm:3bf0c14a2f4afad3`.
- **`xbrl_heldout` will not match B's keys once A′ is ingested.** The ingest's global
  steps are per-filer upserts, so the 12 corpus filers' own rows should not change. But
  every cross-filer view is rebuilt with the A′ filers in it, so a question that ranks or
  compares filers would have a different answer there than its key. Entity resolution
  would also choose among more names than B's author wrote against.
- **B does not depend on A′.** It asks only about the 12 suite filers, and it is
  protected by never having been seen, not by the environment it runs in.
- **Same day as the main suite.** M1–M10 run on 2026-10-28. Running B the same day keeps
  B and the main suite comparable under v1's same-day rule.

## 2. Who sees B's results before publication

- B's author builds and runs B. B's suite file stays outside the repository, and its
  hash is checked against v1 §4 (`a1aae65d…`) before the runs.
- B's runs are stored in `xbrl_db`'s `eval` schema without `--persist-audit`, so no B
  question enters `audit.query_audit`.
- Until the results are published, the assistant that runs the rest of the benchmark
  reads only run-level fields of B's runs (run id, counts, accuracy, cost), never their
  case rows or questions.

## 3. The §7 audit before B's runs

**Change.** v1 §7's third audit item ("no stored `eval` run used a held-out suite") now
means: before B's runs, no stored run used B's, A′'s or P's suite; before A′'s runs, no
stored run used A′'s or P's suite, and B's runs are listed by run id.

**Result of the audit for B, run 2026-10-28 at about 16:25Z:**

1. `audit.query_audit` in `xbrl_db` since v1's merge (2026-10-28T12:49Z): 1,301 queries,
   checked against all 68 A′ pool candidates (the draw is not yet made) and P's 4 new
   filers, by ticker and by name: **no match**. A control run of the same matcher on
   corpus filers found 96–135 queries each.
2. `xbrl_heldout` (cloned 2026-10-28T14:23:28Z): **0 query records since its clone**. Its
   8,032 older rows were copied from `xbrl_db`; the latest is from 2026-10-17.
3. `eval` runs since v1's merge: 13, on the suites `sql_derived` and `sql_derived_slice`
   only. **None used a held-out suite.**
