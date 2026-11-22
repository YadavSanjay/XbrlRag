# XbrlRag dev baseline, 2026-11-10

A dated measurement record: every v2 arm run on the development splits on 2026-11-10,
before any v2 fix, on two providers. Read it for where XbrlRag stood before the fixes and why
the fixes were chosen. It is **dev-only and not evidence for the success bar**: the
pre-registration ([§1](../eval/heldout_v2/preregistration.md#1-the-question-sets-and-the-looks))
reserves dev and validation for "tuning and checking fixes; never evidence for the bar", and
none of these runs used the held-out sets or the decision profile. Where a bar line is checked
here, it is a dry run that shows direction only.

Measured at commit `3581590`, corpus fingerprint `pm:ca0f8f27e42d39d9`, grader v4, on the
frozen suites of 2026-11-10. The `eval` schema (`/tech/eval`; run ids in the
[appendix](#appendix-runs-and-method)) is the source of truth for every number; later
regrades of these runs under newer graders give slightly different figures.

**In this doc:** [Summary](#summary) · [Arms and providers](#arms-and-providers) ·
[Headline results](#headline-results) · [By kind and shape](#by-kind-and-shape) ·
[Against the comparators](#against-the-comparators) ·
[Provider comparison](#provider-comparison) · [Failure classes](#failure-classes) ·
[Measurement findings](#measurement-findings) · [Limits](#limits) ·
[Appendix](#appendix-runs-and-method)

## Summary

**What was measured.** `xbrlrag_full` on the full dev (628) and validation (272) suites;
`frontier_sec_tools` and `long_context_oracle` on a fixed 100-case dev sample;
`naive_rag_filtered` and `closed_book` on dev. Each arm ran on the `anthropic` and the
`deepseek` profiles.

**Headline.** XbrlRag scored 79.3% (Anthropic) and 78.7% (DeepSeek) value accuracy on dev.
On the 100-case sample the Opus tools agent led with 89 against XbrlRag's 78, so the
direction of bar line 1 was **not met on dev**; XbrlRag was 4.6× to 24× cheaper per question.

**Findings that mattered:**

1. **Single-filer numerics were at the ceiling:** 94% on both providers, 3 points behind the
   long-context oracle (bar line 3's dry run held).
2. **Cross-filer was the gap, and it was missing plan operations, not data:** `screen.sum`
   0 of 31, `screen.median` 5 of 19 and CAGR 38–42%, because the plan had no aggregate or
   annualising step. Peer rank (2 of 30) failed on an incomplete universe.
3. **Honesty held:** silent-wrong 0.5–1.0% on dev, 0 on validation. About three quarters
   of declines were those missing operations, so the system declined instead of inventing.
4. **Accuracy was set by architecture, not model:** the two providers agreed on 97.5% of
   dev cases, and every failure class appeared at nearly the same count on both. The
   tools agent, by contrast, moved 6 points with its model.
5. **The measurement had gaps:** grounding precision was not computed, baseline paraphrase
   stability was an artefact, and DeepSeek's verifier silently failed to run on 7% of
   answers.

These findings set the order of the fixes phase: aggregate and CAGR operations, the ratio
step, universe coverage for peer rank, and TSM currency.

## Arms and providers

| Arm | What it is | Suites |
|---|---|---|
| `xbrlrag_full` | The product | dev (628), validation (272) |
| `frontier_sec_tools` | Frontier model with tools over SEC's public APIs | dev sample (100) |
| `long_context_oracle` | The model handed the right filing | dev sample (100); 19 cross-filer cases have no single filing and are excluded |
| `naive_rag_filtered` | Basic RAG over the filing text, filtered to the filer | dev (628) |
| `closed_book` | The model alone | dev (628) |

| Profile | Models served |
|---|---|
| `anthropic` | Sonnet 5.5 for `xbrlrag_full` stages (Haiku 5.5 verifier) and the baselines; Opus 5.5 for the tools agent |
| `deepseek` | deepseek-flash for `xbrlrag_full` (v4-pro on escalation), `closed_book` and `naive_rag`; v4-pro for the tools agent and long context |

The dev sample is a seeded, shape-stratified subset of dev (one phrasing per intent), so
XbrlRag's sample results are its dev-run answers for those cases. All cross-arm comparisons
use **value accuracy** (the right number, citations aside), because the baselines cite no
`fact_id`; `xbrlrag_full`'s plain accuracy (which also needs a resolving citation) is shown
beside it. Bar kinds follow pre-registration §3 by suite shape; these suites had no compound
cases.

## Headline results

| Arm | Provider | Split | n | Accuracy | Value acc. | Silent-wrong | Confident-wrong | Declines | $/case | p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| `xbrlrag_full` | Anthropic | dev | 628 | 79.1% | 79.3% | 1.0% | 1.1% | 10.5% | 0.019 | 12 / 22 s |
| `xbrlrag_full` | DeepSeek | dev | 628 | 78.5% | 78.7% | 0.5% | 0.6% | 10.0% | 0.004* | 13 / 44 s |
| `xbrlrag_full` | Anthropic | val | 272 | 72.4% | 74.3% | 0.0% | 0.0% | 9.6% | 0.021 | 13 / 39 s |
| `xbrlrag_full` | DeepSeek | val | 272 | 69.5% | 71.0% | 0.0% | 0.0% | 12.1% | 0.004* | 17 / 55 s |
| `frontier_sec_tools` | Opus 5.5 | sample | 100 | — | 89.0% | 0.0% | 6.0% | 5.0% | 0.090 | 29 / 86 s |
| `frontier_sec_tools` | v4-pro | sample | 100 | — | 83.0% | 0.0% | 11.0% | 3.0% | 0.054* | 25 / 350 s |
| `long_context_oracle` | Sonnet 5.5 | sample | 100 | — | 69.0%† | 0.0% | 2.0% | 8.0% | 0.204 | 56 / 62 s |
| `long_context_oracle` | v4-pro | sample | 100 | — | 69.0%† | 0.0% | 1.0% | 9.0% | 0.069* | 50 / 76 s |
| `naive_rag_filtered` | Sonnet 5.5 | dev | 628 | — | 45.1% | 0.0% | 4.0% | 48.1% | 0.014 | 3 / 20 s |
| `naive_rag_filtered` | flash | dev | 628 | — | 41.9% | 0.0% | 7.2% | 46.0% | 0.002* | 4 / 13 s |
| `closed_book` | Sonnet 5.5 | dev | 628 | — | 30.6% | 0.0% | 3.5% | 61.9% | 0.004 | 3 / 7 s |
| `closed_book` | flash | dev | 628 | — | 20.4% | 0.0% | 7.2% | 71.8% | 0.001* | 3 / 9 s |

\* DeepSeek cost is tracked at peak price; these runs ran off-peak, so the bill was about
half. † Of 100, with the 19 excluded cross-filer cases counted as fails; on the 81 it can run,
both providers score 85.2%. Declines are refused plus hedged. Total spend: Anthropic $58.07,
DeepSeek $18.86 tracked.

## By kind and shape

Value accuracy on dev, exclusions counted as fails:

| Bar kind | n | XbrlRag A | XbrlRag D | closed A | closed D | naive A | naive D |
|---|---|---|---|---|---|---|---|
| single-filer lookup + compute | 439 | **94.3%** | **94.1%** | 40.8% | 26.7% | 60.4% | 56.0% |
| cross-period | 61 | 67.2% | 63.9% | 11.5% | 9.8% | 29.5% | 27.9% |
| cross-filer | 128 | **33.6%** | **32.8%** | 4.7% | 3.9% | 0.0% | 0.0% |

| Shape | n | XbrlRag A | XbrlRag D | What failed |
|---|---|---|---|---|
| `lookup` | 210 | 95.2% | 95.2% | mostly TSM |
| `compute.ratio` | 185 | 93.5% | 93.5% | TSM; ratio asked, components answered |
| `compute.yoy_growth` | 20 | 100% | 95.0% | — |
| `compute.difference` | 24 | 87.5% | 87.5% | a GOOGL contract-liabilities key under adjudication |
| `point_in_time` | 18 | 94.4% | 94.4% | — |
| `trend` | 19 | 73.7% | 68.4% | TSM; HD debt periods |
| `compute.cagr` | 24 | **41.7%** | **37.5%** | plan computed end/start ratio, never annualised |
| `screen.top` | 31 | 74.2% | 74.2% | membership at the margin |
| `screen.bottom` | 17 | 76.5% | 76.5% | top-1 at the margin |
| `screen.median` | 19 | **26.3%** | **26.3%** | no median step |
| `screen.sum` | 31 | **0.0%** | **0.0%** | no sum step |
| `peer_relative` | 30 | **6.7%** | **3.3%** | rank over an incomplete universe |

Notes on the pattern:

- **Ranking screens worked where they returned rows** (mean rank correlation 1.00); misses
  were rank 3 versus 4 and top-1 swaps.
- **Sums were refused correctly:** in one dividends case XbrlRag returned exactly the right
  three filers, whose values sum to the key, then declined to add them because no compute
  step produced the sum. The synthesizer's rule was right; the plan lacked the operation.
- **Filer weak spots:** TSM (20-F, IFRS, TWD) 0 of 14 on both providers, a currency defect;
  HD (52/53-week, January year-end) 60.9%. Every other filer with 8 or more cases scored at
  least 72.7%.
- **Paraphrase stability** (same value grade across an intent's phrasings): dev 94.8% (A) and
  91.3% (D) over 231 intents; validation 91.3% and **87.0%** over 69 intents, the latter below
  the 90% bar line.
- **Validation was lower only because of its mix:** 36% cross-filer against dev's 20%.
  Reweighted to dev's mix it scored 83.9% (A) and 80.7% (D), so dev results carried over.

## Against the comparators

All arms on the same 100 sample cases (counts correct):

| Group | XbrlRag A | XbrlRag D | Agent Opus | Agent v4-pro | Long-ctx Sonnet | Long-ctx v4-pro | naive A | closed A |
|---|---|---|---|---|---|---|---|---|
| all 100 | 78 | 76 | **89** | 83 | 69 | 69 | 45 | 34 |
| single-filer lookup + compute (67) | 63 | 63 | 67 | 66 | 65 | 65 | 40 | 32 |
| cross-period (9) | 5 | 4 | **9** | **9** | 4 | 4 | 5 | 1 |
| cross-filer (24) | 10 | 9 | **13** | 8 | 0‡ | 0‡ | 0 | 1 |
| cross-filer + cross-period (33) | 15 | 13 | **22** | 17 | 4 | 4 | 5 | 2 |

‡ 19 of 24 excluded by design; both models miss the other 5 (`peer_relative`).

| Paired exact McNemar (indicative, small n) | Pool | XbrlRag only / other only | p |
|---|---|---|---|
| XbrlRag A vs Agent Opus | all 100 | 2 / 13 | 0.007 |
| XbrlRag A vs Agent Opus | cross-filer + cross-period | 2 / 9 | 0.065 |
| XbrlRag D vs Agent Opus | cross-filer + cross-period | 1 / 10 | 0.012 |
| XbrlRag A vs Agent v4-pro | cross-filer + cross-period | 5 / 7 | 0.77 |
| XbrlRag D vs Agent v4-pro | cross-filer + cross-period | 4 / 8 | 0.39 |
| XbrlRag A vs Long-ctx Sonnet | single-filer (67) | 2 / 4 | 0.69 |

| Per question | XbrlRag A | XbrlRag D | Agent Opus | Agent v4-pro | Long-ctx Sonnet | Long-ctx v4-pro |
|---|---|---|---|---|---|---|
| mean cost | $0.0197 | $0.0037* | $0.0898 | $0.0540* | $0.2043 | $0.0687* |
| cost per correct answer | $0.025 | $0.005* | $0.101 | $0.065* | $0.296 | $0.100* |
| p50 latency | 12 s | 14 s | 29 s | 25 s | 56 s | 50 s |

Read as dry runs of the bar:

- **Line 1 direction, not met on dev.** The Opus agent led 22 to 15 on the pooled kinds; the
  v4-pro agent and XbrlRag were level. The cases the agent won were almost all the failure
  classes below.
- **Cost line:** XbrlRag cost 22% (Anthropic) and 4.1% (DeepSeek) of the Opus agent per
  question; only the DeepSeek figure is under a tenth. Neither is the bar's number, which is
  read against the pre-registered decision agent.
- **Line 3, held:** on single-filer lookup + compute XbrlRag was 3 points behind the oracle.
- **Long context is no competitor on cross-filer questions** by design; on the 81 cases it
  can run, it matched XbrlRag (85.2% against 84.0%) at 10× to 19× the cost.
- **Naive RAG and closed book are floors.** `closed_book` on Sonnet recalled 30.6% of dev from
  memory, and 85–86% of MSFT and AAPL cases: well-known filers make easy cases easier for
  every model-based arm.

## Provider comparison

Same arm, same cases, provider swapped:

| Arm | Cases | Anthropic | DeepSeek | Only A / only D | p | Value agreement | Cost A:D |
|---|---|---|---|---|---|---|---|
| `xbrlrag_full` dev | 628 | 79.3% | 78.7% | 10 / 6 | 0.45 | **97.5%** | 5.2× |
| `xbrlrag_full` val | 272 | 74.3% | 71.0% | 13 / 4 | 0.049 | 93.8% | 4.8× |
| `frontier_sec_tools` | 100 | 89.0% | 83.0% | 6 / 0 | 0.031 | 94.0% | 1.7× |
| `long_context_oracle` | 100 | 69.0% | 69.0% | 3 / 3 | 1.0 | 94.0% | 3.0× |
| `naive_rag_filtered` | 628 | 45.1% | 41.9% | 21 / 1 | < 0.001 | 96.5% | 8.4× |
| `closed_book` | 628 | 30.6% | 20.4% | 81 / 17 | < 0.001 | 84.4% | 5.3× |

- **XbrlRag's accuracy is architectural.** Its weak spots appear on both providers at nearly
  the same count; they are system defects, not model defects.
- **Arms that lean on the model move with it.** The tools agent gained 6 points with Opus,
  all on cross-filer screens, so it is the comparator whose strength depends most on its
  model. Its confident-wrong rate was 6% (Opus) and 11% (v4-pro), against XbrlRag's
  0.6–1.1%.
- **On validation Anthropic's edge was small but real** (p = 0.049), mostly cross-period
  trends and a few DeepSeek refusals.
- **DeepSeek was about 5× cheaper for XbrlRag at about the same accuracy,** supporting it as
  the development profile. Its p95 latency was twice Anthropic's.
- **Where XbrlRag's LLM cost went** (dev, Anthropic / DeepSeek): synthesizer 53% / 47%,
  plan generator 26% / 11%, intent router 12% / 9%, decomposer 6% / 5%, verifier 2% / 28%.
- **DeepSeek's reasoning could exhaust the completion budget and return nothing.** On
  `xbrlrag_full`, synthesizer cases recovered by escalation, but verifier cases did not:
  **45 of 628 answers (7%) shipped without the faithfulness check**. Anthropic had no LLM
  errors in 3,025 calls.

## Failure classes

Every value-wrong case, classified by ordered rules (first match wins):

| Failure class | Mechanism | Dev A | Dev D | Val A | Val D |
|---|---|---|---|---|---|
| Cross-filer aggregate (sum, median) | No aggregate step over screen rows | 45 | 45 | 34 | 33 |
| Peer rank | Ranked only among filers with the metric mapped | 28 | 29 | 14 | 15 |
| CAGR | End/start ratio, never annualised; one end period not found | 14 | 15 | 3 | 4 |
| TSM (IFRS, TWD) | No usable value for the IFRS filer | 14 | 14 | 4 | 4 |
| Top/bottom membership | Membership at the margin, top-1 swaps | 12 | 12 | 14 | 16 |
| Ratio asked, components answered | No ratio step; confident, so silent-wrong | 5 | 1 | 0 | 0 |
| Trend periods | Debt definition, missing periods | 3 | 4 | 1 | 4 |
| Other | Scattered | 9 | 14 | 0 | 3 |
| **Total value-wrong** | | **130** | **134** | **70** | **79** |

The first five classes held 87% of Anthropic dev losses. Three are missing plan operations
(aggregates, CAGR, the ratio), with an upper bound of about 10 dev points if fully
recovered; peer rank and membership are universe coverage (concept mapping); TSM was the
open currency defect.

## Measurement findings

These concern the evaluation, not the system:

1. **Baseline paraphrase stability read 1.0, an artefact:** it was computed on `passed`,
   which is always false for arms without `fact_id`s. On value accuracy it was 91–93% for
   `closed_book` and 67–68% for `naive_rag`.
2. **Grounding precision was not computed** in these runs, although a bar line needs it.
3. **Single-source keys scored lower** than verified keys (60.0% against 82.1% on Anthropic
   dev), so part of the gap may be key quality; two silent-wrongs needed adjudication against
   the filing.
4. **The tools agent's budget-hit path was untested on Opus and mishandled on v4-pro:** one
   failed final turn was recorded as an error, not a budget hit, so a budget rule counting
   hits would miss it.
5. **A cap halt could mark a complete run ABORTED** when cases in flight crossed the cap.
6. **Cost projections from about 30 cases missed by −36% to +27%;** treat them as ±40%.
   One-case smokes did not predict per-arm cost.

## Limits

- **Nothing about the bar:** no held-out set, no decision profile, 100-case comparisons
  (cross-period has 9 cases, so one case moves it 11 points).
- **No compound, quarterly or text results:** none were in these suites, and the judge did
  not run.
- **Pre-fix only:** the fixes are measured against these runs on slices.

## Appendix: runs and method

| Arm, provider | Suite | Run id | Status |
|---|---|---|---|
| `closed_book`, anthropic | dev | `eval-8eeb235b42d5` | OK (one cap resume) |
| `naive_rag_filtered`, anthropic | dev | `eval-3e15315af22f` | OK (two cap resumes) |
| `xbrlrag_full`, anthropic | dev | `eval-42942572934b` | OK |
| `xbrlrag_full`, anthropic | validation | `eval-41b4ab7e7015` | OK |
| `frontier_sec_tools`, deepseek-v4-pro | 30 of dev sample (mechanics check, not compared) | `eval-823ad2613066` | OK, arm frozen |
| `frontier_sec_tools`, claude-opus-5-5 | dev sample | `eval-5471b3c99d92` | OK |
| `frontier_sec_tools`, deepseek-v4-pro | dev sample | `eval-f8e487f196c1` | OK, 1 error |
| `xbrlrag_full`, deepseek | dev | `eval-95c14a787892` | OK |
| `xbrlrag_full`, deepseek | validation | `eval-b836ff5b0052` | OK |
| `long_context_oracle`, claude-sonnet-5-5 | dev sample | `eval-e9597211e79f` | OK, 19 exclusions |
| `closed_book`, deepseek | dev | `eval-5a2935d1b160` | OK |
| `naive_rag_filtered`, deepseek | dev | `eval-3193dd00c67e` | OK ($0 resume to clear the cap flag) |
| `long_context_oracle`, deepseek-v4-pro | dev sample | `eval-783b5cb3ab14` | OK, 19 exclusions + 1 error |

**Frozen tools agent:** commit `3581590`, prompt `_base/frontier_sec_tools/v2`, tools
fingerprint `cd4d26db07f666dd0874be9422da6589975ca63e8d95c1a663a9e957db76249d`, budgets
20 steps / 60 tool calls / $2 per case / 250,000-token window, SEC snapshot as of 2026-10-29.

**Suites:** the freeze of 2026-11-10, kept in `data/eval/v2_frozen_2026-11-10/`
(`xbrl_v2_dev.json` 628, `xbrl_v2_validation.json` 272, `xbrl_v2_dev_sample.json` 100) and
recorded in pre-registration Amendment 1.2.

**Method:** figures computed from the per-case results of the stored runs (matching the
`eval` schema rows); stage costs and errors from `audit.query_event`; McNemar is the
two-sided exact binomial test on discordant pairs; intervals are Wilson 95%; paraphrase
stability recomputed per intent on value accuracy.
