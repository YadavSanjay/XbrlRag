# XbrlRag blog v1: pre-registration v1, the method

**Status:** sealed by the merge of this file into `main`, timestamped like v0:

- the time GitHub records for that merge;
- an OpenTimestamps proof over the SHA-256 hashes listed in [`HASHES.txt`](HASHES.txt).

The tag `blog-v1-freeze` is put on that merge commit.

**Relation to v0:** [pre-registration v0](preregistration_v0.md) sealed the prospective set
(P). This document adds everything else that has to be fixed before any measured run:

- the systems and metrics;
- the four question sets;
- the statistics;
- the list of runs;
- the quarantine rules;
- the rule for the headline.

It adds detail to v0 and changes nothing in it. Pre-registration **v2** will add the A′
draw output, the A′ suite hash and the held-out lock time; it may add detail but may not
change this document. Any change is recorded as a numbered amendment with its date and
reason.

## 1. What is frozen

**Code:** commit `8c41daa2a247995be60c7af5f2bda1b037fb766a`. The merge that adds this file
changes nothing outside `docs/eval/blog_v1/`, so the code at the tag is the code at that
commit. Every measured run
is made at the tag `blog-v1-freeze`. No code changes between the freeze and the last run,
except a disclosed generic fix under §8.

**The freeze record:** [`freeze_record.json`](freeze_record.json), written by
[`freeze_record.py`](freeze_record.py) on 2026-10-27 at that commit with a clean tree. It
records:

| Item | Value |
|---|---|
| Grader | version 3 (`docs/specs/arm-neutral-grading.md`) |
| Main suite | `test/eval/suites/sql_derived_v1.json`, 482 cases, corpus fingerprint `pm:3bf0c14a2f4afad3` (the live corpus had the same fingerprint) |
| Stage tiers | `XBRL_STAGE_TIERS` in `src/llm/profiles.py` |
| Per profile and stage | the requested model, its prompt reference and the prompt content hash (`prompt_version`) |
| Served model ids | one probe call per model, with the id the provider reported (table below) |
| Services | PostgreSQL 18.4; Qdrant 1.18.2 (commit `44ad62f8`); RagInference commit `7cc6abd0` serving `BAAI/bge-m3` and `jinaai/jina-reranker-v3` |
| Packages | Python and key package versions |

**Served model ids at the freeze:**

| Profile | small | medium | frontier |
|---|---|---|---|
| `openai` | `gpt-5-nano` → `gpt-5-nano-2025-08-07` | `gpt-5-mini` → `gpt-5-mini-2025-08-07` | `gpt-5` → `gpt-5-2025-08-07` |
| `openai-5.6` | `gpt-5.6-luna` → same | `gpt-5.6-terra` → same | `gpt-5.6-sol` → same |
| `anthropic` | `claude-haiku-5-5` → same | `claude-sonnet-5-5` → same | `claude-opus-5-5` → same |

"Same" means the provider reported the requested id itself; that is the most specific id
it publishes. Every measured run records its own served ids in its stored
`config.served_models` (`docs/specs/served-model-id.md`). **A run whose served ids differ
from this table is reported with that difference.** It is not silently pooled with the
others.

## 2. Systems

All are run with `scripts/eval/run_suite.py --system <name>` at the freeze tag.

| System | What it is |
|---|---|
| `xbrlrag_full` | The XbrlRag pipeline: plan → SQL facts → cited synthesis → verifier |
| `naive_rag_plain` | Top-10 hybrid search over the same Qdrant chunks, no reranker, one LLM call at the synthesizer's tier |
| `naive_rag_filtered` | The same, with the search filtered to the question's filer and fiscal year |
| `closed_book` | No documents; "I don't know" is allowed. A control for unaided recall |
| `long_context` | The question's whole filing in the prompt (an oracle document), on `gpt-5.6-sol` and on `claude-opus-5-5` |

**Profiles:** the **`openai` profile** is primary, and every run in §6 uses it unless the
run says otherwise. `xbrlrag_full`, `naive_rag_*` and `closed_book` use the profile's
tiers. Every baseline uses the same tier as XbrlRag's synthesizer, so a comparison is not
a comparison of models. `long_context` names its model explicitly. The `openai-5.6` and
`anthropic` profiles appear only in the secondary portability runs (§6, runs M7–M10).

**Long-context exclusions:** screens, and any filing over 250K estimated tokens. Each
excluded case is reported as excluded, not dropped.

## 3. Metrics (grader v3)

- **Value accuracy:** the number, or the ranked rows, are right within the case's
  tolerance. This is how systems are compared.
- **Confident-wrong:** a wrong number or row set given without refusing or hedging. It is
  applied the same way to every system.
- **Refusal rate,** and the **hedged** rate.
- **Accuracy:** a right value **and** a right cited fact. XbrlRag only. The baselines cite
  no `fact_id`s, so this is never quoted for them.
- **Coverage:** structural exclusions are counted and reported.
- **Cost:** LLM API spend per question, as the gateway records it. Infrastructure and
  ingest are excluded. **Latency:** p50 and p95.

**Headline slice:** lookup + compute cases. Screens, breakdowns and dimensional cases are
reported separately, by kind, with the reason they are kept apart.

## 4. The question sets

| Set | Rule | Fixed by |
|---|---|---|
| **Main suite** | The 482 committed cases. It is also the development set, and the post says so | The suite file's hash in the freeze record |
| **A′, new filers** | §4.1 | The pool hash and the beacon minute here; the draw, suite hash and lock time in v2 |
| **B, hand-written** | 47 questions on the 12 suite filers, written by the repo owner outside the repo, all checked by `scripts/eval/set_b.py check-keys` (SQL only) | The hash below |
| **P, prospective** | v0, unchanged | v0 |

**B's hash** (the sealed file, which is kept outside the repository and not opened until
the held-out runs):
`a1aae65dc0f329a497a3fc7a0ce5859920313f31a0c0ff3fe3bd378bdd021094  heldout_b_v1.json`.
Nobody but its author has seen B's questions. They have never been run through any system.

### 4.1 A′, new filers

- **Pool:** [`aprime_pool.json`](aprime_pool.json), built by `scripts/eval/aprime_draw.py
  pool` (spec `docs/specs/aprime-draw.md`). It starts from the S&P 100 constituents list in
  [`aprime_constituents.csv`](aprime_constituents.csv), Wikipedia revision `oldid=1378987012`
  (a table dated 2026-09-21, citing iShares OEF). It removes:
  - Financials and Real Estate;
  - the 15 corpus filers;
  - the 5 P filers;
  - duplicate share classes.
  Result: **S 20, D 48.**
- **Strata:** S is GICS Information Technology + Communication Services; D is every other
  remaining sector.
- **Randomness:** the NIST Randomness Beacon 2.0 pulse for **2026-11-02T15:00Z**. This
  minute must be later than the merge of this file. If the merge happens later than
  2026-11-01T15:00Z, the minute is moved here, before the merge, to a minute at least 24
  hours after it.
- **Draw:** `scripts/eval/aprime_draw.py draw --pulse-time 2026-11-02T15:00Z`, run at or
  after that minute. Each stratum's candidates are ranked by
  `sha256("xbrlrag-aprime-v1|" + outputValue + "|" + cik10)`. The walk in rank order takes
  the first 5 **eligible** filers as drawn and the next 2 as ordered alternates.
  **Eligible** means an original 10-K in inline XBRL for each of fiscal years 2023–2025,
  checked from EDGAR filing indexes only. A stratum that runs out of eligible candidates
  reports a shortfall; it is not refilled.
- **Data:** each drawn filer's FY2023–2025 10-Ks, plus its FY2026 10-K if EDGAR shows one
  filed before the beacon minute. FY2023 is context for year-over-year questions only.
- **Ingest:** only into the held-out environment (§7), with the full ingest flow at the
  freeze tag.
- **Failure rule:** a drawn filer whose ingest fails, or which the publish gate holds back,
  is replaced by its stratum's next alternate, in order, and the replacement is listed.
  Once the alternates are used up, the stratum is reported short. No fix specific to one
  filer is made. A generic fix is handled under §8.
- **Suite:** a copy of `configs/eval/sql_derived_templates.yaml` at the freeze, with only
  `corpus.tickers`, `company_names` and the years (`[2024, 2025, 2026]`) changed. It is
  built by the suite builder at the freeze tag, tier `standard`, against the held-out
  environment. It is built blind and hashed (hash in v2), and never opened before the
  runs. Screens are kept and reported separately.

### 4.2 The long-context cases

The 60 cases are fixed now: [`long_context_cases.json`](long_context_cases.json), selected
from the main suite by [`long_context_select.py`](long_context_select.py) with seed
`xbrlrag-longctx-v1`. The rule:

1. Only lookup and compute cases are candidates.
2. A filing (filer and fiscal year) is eligible if it has at least 2 lookups and 1 compute.
   27 filings are eligible.
3. 20 filings are chosen by a hash-ordered round-robin over the 12 filers, so 8 filers
   get two filings and 4 get one.
4. From each filing, the 2 lookups and 1 compute with the lowest hash keys are taken.

The list holds 40 lookups and 20 computes. The two models get the same cases. Some of
the 10 cases of the earlier pilot may be among them; the post says so.

### 4.3 Showcase examples

The side-by-side examples in the post are drawn, not chosen. From the main-suite runs M1
and M3, cases are ranked by `sha256("xbrlrag-showcase-v1|" + case_id)`, ascending. The
post shows:

- the first 4 cases in that order;
- the first case in that order where a baseline was right and XbrlRag declined, if one
  exists;
- the first case in that order where XbrlRag was confident-wrong, if one exists.

One full audit trail (answer → `fact_id` → SQL → filing) is shown for the first of the
4 cases that XbrlRag answered.

## 5. Statistics

- **Intervals:** every proportion is reported as a count over n with a Wilson 95%
  interval.
- **Repeats:**
  - A system run three times is reported as the mean of its three runs, with the range.
  - For paired tests, a case counts as passed by that system when it passes in at least
    2 of the 3 runs.
  - A system run once uses that run.
- **Comparisons:**
  - XbrlRag is tested against each baseline with a two-sided exact McNemar test on the
    cases both answered in the same set.
  - The p-values are reported as they are, with no threshold claimed as a finding.
  - Main-suite differences smaller than about 2 points are described as within noise.
    Repeat runs vary with sd ≈ 0.9 points.
- **Separation:**
  - The main suite, A′, B and P are reported separately and **never pooled**.
  - A′ is also reported by stratum.
  - P's new filers and AAPL are also reported separately, as v0 states.
- **Misses on the held-out sets:** every miss is classified as a system error, a
  ground-truth error or out of scope, and the counts are published.

## 6. Runs

Each run has a hard `--max-cost-usd` cap, and its `run_id` is recorded. A run that hits
its cap is rerun once in full, and both run ids are disclosed. Same-day rule: runs that
are compared with each other are made on the same day, at the freeze tag.

**Main suite, all on one day:**

| # | System | Profile / model | Cases | Repeats | Cap per run |
|---|---|---|---|---|---|
| M1 | `xbrlrag_full` (`--persist-audit`) | `openai` | 482 | 3 | $6 |
| M2 | `naive_rag_plain` | `openai` | 482 | 1 | $4 |
| M3 | `naive_rag_filtered` | `openai` | 482 | 3 | $4 |
| M4 | `closed_book` | `openai` | 482 | 1 | $3 |
| M5 | `long_context` | `gpt-5.6-sol` | the 60 cases | 1 | $40 |
| M6 | `long_context` | `claude-opus-5-5` | the 60 cases | 1 | $40 |
| M7 | `xbrlrag_full` | `openai-5.6` | 482 | 1 | $15 |
| M8 | `naive_rag_filtered` | `openai-5.6` | 482 | 1 | $10 |
| M9 | `xbrlrag_full` | `anthropic` | 482 | 1 | $15 |
| M10 | `naive_rag_filtered` | `anthropic` | 482 | 1 | $10 |

M5 and M6 run over a suite file built from the 60 ids with
`scripts/eval/build_slice_suite.py --target`, in filing order, with `--workers 4`. M7–M10
are a secondary "does the design hold across model families" table. They are never the
headline.

**Held-out runs (A′ and B), on one day, after the audit in §7:**

| # | System | Sets | Repeats | Cap per run |
|---|---|---|---|---|
| H1 | `xbrlrag_full` | A′, B | 3 each | $3 |
| H2 | `naive_rag_filtered` | A′, B | 3 each | $2 |
| H3 | `long_context` (both models) | B | 1 each | $10 |

H3 runs only if the total spend recorded before the held-out day is under $150. Otherwise
it is skipped, and the post says so.

**P, after its 2026-11-30 cutoff:** `xbrlrag_full` ×3, `naive_rag_filtered` ×3 and
`closed_book` ×1, with a same-day control of `xbrlrag_full` ×1 on the main suite. P's
suite is built and hashed as v0 §5 states.

## 7. Quarantine

**Held-out environment:**
- Postgres database `xbrl_heldout`, cloned from `xbrl_db` at the freeze;
- a separate Qdrant instance;
- separate filing roots.

A′ and P data go only there. After the A′ and B suites are built, the environment is
locked: Qdrant is stopped and the database refuses connections. It is unlocked only for
the held-out runs.

**Until the held-out runs:**
1. The held-out suite files are not opened, run or searched, and no slice includes them.
2. No queries, UI questions, traces or SQL are run against the held-out environment, and
   nothing about an A′ or P filer is run in any environment.
3. No P filing goes into `xbrl_db`, and no A′ or P ticker is added to the main downloads
   configuration.
4. Code changes that affect every filer are allowed. They are recorded under §8 if made
   after the freeze.
5. Any accident is logged with its date, what happened and why, and published in the post.

**Operators while ingesting** look only at exit codes, stage-level counts and publish-gate
verdicts, never at facts, values, chunks or traces.

**The audit before the held-out runs, published with the results:**
- `audit.query_audit` in `xbrl_db` has no query naming an A′ or P filer since the merge of
  this file;
- `xbrl_heldout` has no query records since its clone;
- no stored `eval` run used a held-out suite.

## 8. Exclusions, fixes and failures

- **Generic fixes:** a code fix found after the freeze is allowed only if it is generic
  (not specific to one filer or case). It is disclosed with its commit. Every result
  measured after it is labelled "after fix N", and the main-suite runs are repeated on
  the fixed code so the comparison stays same-day.
- **Ingest and publish-gate failures** are handled as §4.1 says for A′ and as v0 §7 says
  for P.
- **Errored cases** (a system error rather than an answer) count as not passed for value
  accuracy, and they are reported by count.

## 9. Publication and the headline

- **Results decide the headline.** The title, introduction and summary of the post are
  written only after the held-out runs. If a baseline comes close to XbrlRag, the post
  says so, and its story becomes auditability, declining instead of guessing, and cost.
- **Everything is published:** every run listed in §6 is reported, whatever it shows,
  with its `run_id`, this document and its proofs.
- **P's timing:** the main post may be published before P's cutoff. In that case P is
  added as a **dated update to the same post by 2026-12-14**, whatever it shows. If
  everything else is ready within about a week of the cutoff, the post may instead launch
  with P included. The cutoff (2026-11-30) does not move. This states when v0's
  "alongside the main results" is met.
- **Wording:** the post uses "confident-wrong" for every system. It calls the main suite
  the development set. It describes the SEC comparison as "expected values match the
  SEC's published XBRL figures (n, by kind)", and quotes cost as LLM API spend per
  question.
