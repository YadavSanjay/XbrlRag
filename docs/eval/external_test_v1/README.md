# External test v1: the first test outside the development loop

**Run on 2026-10-28. Closed on 2026-10-29.**

Until October 2026, XbrlRag was measured only on its own 482-case suite: questions generated
from SQL templates, with answer keys from the same database views the pipeline queries, and
a pipeline tuned on that suite until it scored 96.7%. This exercise was XbrlRag's **first
real-world test outside that closed loop**. It fixed its rules in advance, sealed a set of
questions written by hand, and compared the pipeline with simpler systems at the same time.

This page records what was run, what it showed, why the in-loop number misled us, and
what changes as a result.

## How the test was set up

- **Rules fixed before the runs.** A pre-registration stated the question sets, the
  systems, the metric (value accuracy), the repeat rule and the statistics before any run.
  Its documents were hashed and timestamped with OpenTimestamps (`sealed/`).
- **A code freeze.** Every run used one commit (`8f94545`), recorded with its models,
  prompts, grader and corpus in `sealed/freeze_record.json`.
- **A hand-written question set nobody tuned on (set B).** 47 questions written by the
  maintainer about the same filers and years, kept outside the repository, hashed before
  the runs and never shown to the AI coding assistant that did the tuning work. Only
  aggregate results were shared.
- **Comparison systems** run on the same questions, the same day:
  - `xbrlrag_full`: the product pipeline;
  - `naive_rag_filtered`: one hybrid search over the same chunks, filtered to the filer
    and year;
  - `long_context`: the whole annual filing handed to gpt-5.6-sol or claude-opus-5-5. Its
    filing selection is an oracle: it is given the right filing.
- **Statistics:** value accuracy with Wilson 95% intervals. A system run three times
  passes a case when it passes in 2 of 3 runs. Systems are compared with two-sided exact
  McNemar tests on the questions both were given, counting a decline as a fail
  ([`eval_stats.py`](../../../scripts/eval/eval_stats.py)).

Two more sets were planned: one on newly drawn filers, and one on filings published after
the freeze. Both were **cancelled on 2026-10-29**, before anything was drawn, ingested or run
(see the decisions below).

## Results

### Main suite (in the loop)

| System | Value accuracy |
|---|---|
| XbrlRag ×3 | 97.1–97.9%; lookup + compute 99.0%; 0 confident-wrong |
| Naive RAG filtered ×3 | lookup + compute 45.9–46.9% |
| Long-context (the 60 cases with a single filing) | gpt-5.6-sol 91.7%, opus 96.7%. Against XbrlRag 5/0 (p 0.06) and 2/0 (p 0.5) |

Run ids: XbrlRag `eval-3bc118489ef4`, `eval-8a47d62d4472`, `eval-0aab4438d8aa`; naive
`eval-102970471bf6`, `eval-0add24793edf`, `eval-daaa8a13fcb2`; long-context
`eval-c2cf88c50d27` (sol), `eval-a26a7035a6a4` (opus). The OpenAI-5.6 and Anthropic profile
runs of XbrlRag are `eval-661a0e744354` and `eval-c8327c041039`.

### Set B (outside the loop)

| Slice (n) | XbrlRag ×3, mean (range) | Naive filtered ×3 | Long-context, both models | Long-context vs XbrlRag |
|---|---|---|---|---|
| Lookup + compute (32) | 79.2% (75.0–84.4) | 57.3% | 90.6% | 2/5, p 0.45 |
| Lookup (16) | 81.2% (13/16 in every run) | 45.8% | 100% | 0/3, p 0.25 |
| Compute (16) | 77.1% | 68.8% | 81.2% | 2/2, p 1 |
| Dimensional (9) | 70.4% | 88.9% | 100% | 0/3, p 0.25 |
| Breakdown (6) | 61.1% | 44.4% | 100% | 0/2, p 0.5 |
| **All (47)** | **75.2% (72.3–80.9)** | 61.7% | **93.6%** | **2/10, p 0.039** |

Run ids: XbrlRag `eval-91c5920da80b`, `eval-cb35bed082ea`, `eval-360524fd9145`; naive
`eval-30a420e1e526`, `eval-aedad1444818`, `eval-fdd5920afb03`; long-context
`eval-0e39584ca100` (sol), `eval-ac0a0fab21a0` (opus). B cost $18.55.

What B shows:
- **A strong model reading the right filing beat XbrlRag on all 47 questions** (p 0.039).
  On the main suite the two had looked equal.
- **XbrlRag lost about 22 points** of value accuracy from the main suite to B (97% to 75%).
  About 7 of those points are declines; each run also gave 2 confident wrong answers,
  against 0 on the main suite.
- **XbrlRag misses the same 3 lookups in every run.** The gap is systematic, not noise.
- **Naive RAG improved on B** (lookup + compute 46% to 57%) and beat XbrlRag on dimensional
  questions. B is not simply harder. It is harder for XbrlRag's routing and planning.
- **B is small:** intervals are about ±15 points on 32 cases. Only the long-context result
  on all 47 is beyond chance.

## Why the in-loop number misled us

1. **The test and the system share their source.** The main suite's questions come from
   templates over `analyst_queries.sql`. Its answer keys come from the same materialized
   views and concept mappings the pipeline queries. A question that maps to those views
   agrees with its key almost by construction, and a mapping error shared by both sides is
   invisible. The [SEC companyfacts check](sec_check/README.md) confirmed that the keys
   match the SEC's XBRL extraction, but that is the same XBRL, not an independent source.
2. **We tuned on that suite until it scored 96.7%.** The accuracy work fixed the suite's
   failures one at a time: router and planner prompts, query-time fallbacks and concept
   mappings. Validating on slices of the same suite could not catch overfitting.
3. **One voice.** The committed suite has one phrasing per case. `paraphrase_stability`
   exists as a metric but was never measured.
4. **No held-out wording during development.** Set B was the first test outside the loop,
   and it came at the end, when it was most expensive to learn from.
5. **An early warning was misread.** On the main suite, long-context and XbrlRag disagreed
   on only 5 and 2 of 60 cases. We read that as "XbrlRag matches a frontier model". It
   actually meant this suite could not separate the two.
6. **The comparison favoured long-context.** It covered single-filer lookup and compute,
   with the right filing handed over. XbrlRag's design matters most elsewhere: cross-filer
   screens, cost (about 30 times cheaper on B), an audit trail for every number, and
   declining instead of guessing. B had no screens, and a decline counted as a fail. This
   explains part of the result; it does not excuse the 22-point drop.

## What went right

- Because the rules, the questions and the code were fixed in advance, the result could not
  be explained away or quietly re-run.
- B was written outside the tuning loop, and only aggregates came back from it.
- The tooling built for this test carries forward: arm-neutral grading, the comparison
  systems, the SEC companyfacts check, the set B tooling, the seeded filer draw and the
  statistics.

## Decisions (2026-10-29)

- **The result stands as the first out-of-loop measurement** of XbrlRag. The main-suite
  figure is reported as in-loop wherever the repo quotes it
  ([README](../../../README.md#how-it-is-measured),
  [evaluation design §1](../../design/XbrlRag_Evaluation.md#1-current-result)).
- **The new-filer and prospective sets are cancelled.** The new-filer set's questions would
  have come from the same templates, so it would have tested new filers in the old voice.
  The next version's held-out filers answer that question with a better design.
- **B is not used to fix the system.** Its author may classify the misses by failure class
  (wording, entity, period, concept, answer key, other) and share the counts.
- **The next version rebuilds the evaluation first:** varied phrasing, answer keys
  independent of the pipeline's views, separate dev, validation and held-out sets, held-out
  filers, and grading of text answers.

## Files

`sealed/` holds the record as it was fixed before the runs. Its files are kept
**byte-identical**, so each SHA-256 in the `HASHES*.txt` manifests and each OpenTimestamps
proof (`*.ots`) still verifies. Their contents are left as written, including the folder
path and the names used at the time (`docs/eval/blog_v1/`, the tag `blog-v1-freeze`).
Verify a file at the commit that sealed it: `git show <commit>:<path> | sha256sum`.

| File | What |
|---|---|
| `sealed/preregistration_v0.md`, `_v1.md`, `_v1_amendment_1.md`, `_v1_amendment_2.md` | The rules fixed before the runs |
| `sealed/HASHES*.txt`, `sealed/*.ots` | SHA-256 manifests and their OpenTimestamps proofs |
| `sealed/freeze_record.json`, `sealed/freeze_record.py` | What the code freeze pinned |
| `sealed/long_context_cases.json`, `sealed/long_context_select.py` | The 60 main-suite cases given to long-context |
| `sealed/aprime_constituents.csv`, `sealed/aprime_pool.json` | The S&P 100 list and the new-filer pool (never drawn) |
| `sealed/prospective_spec.yaml`, `sealed/edgar_check.py`, `sealed/edgar_check_2026-10-21.txt` | The prospective set's rule and its EDGAR check (never run) |
| `sec_check/` | The answer keys checked against SEC companyfacts, and the hand check |
