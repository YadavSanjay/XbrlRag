# v2 measurement plan

**Status:** decided by the maintainer on 2026-11-03. The decisions take formal effect when
Amendment 1 of [preregistration.md](preregistration.md) is merged. Where this plan and the
merged pre-registration disagree, the pre-registration holds until the amendment changes it.

How figures are marked:

- **[M] measured:** a recorded figure, with its source.
- **[V] verified:** checked on 2026-11-03 against the provider's own page.
- **[E] estimate:** arithmetic on stated assumptions, shown so a reviewer can recompute it.

## 0. Changes decided 2026-11-05 and 2026-11-06

These supersede the items they name. Amendment 1 (D2, D4 to D6) carries them into the
pre-registration.

| Change | Supersedes | Reason |
|---|---|---|
| `long_context_resolved` and `long_context_multi` are dropped from every set, dev included. `long_context_oracle` stays, single-filer only. | Decision 6; §3 rows; §6 lines for both arms; §12 item 4 | Pasting whole filings into chat is not a realistic enterprise workflow; neither arm is read by the bar. The oracle is the ceiling bar line 3 needs. |
| `html_rag` is dropped from every set, dev included, and bar line 2 is withdrawn (decided 2026-11-06). | §3 rows; §2's "§3 and §4 are untouched"; `html_rag`'s cost lines in §6 and §9 and its rerank time in §8 | The comparison that matters is XbrlRag against `frontier_sec_tools`. Decision record: `docs/design/XbrlRag_HtmlRag_Decision.md`. |
| `frontier_sec_tools` also runs on `deepseek-v4-pro`, on H-F, H-X and the dev sample. | §3 matrix | The cost bar must hold against a cheaper agent with the same tools, or the claim is about model price. |
| A DeepSeek column of the dev sample for every baseline arm. | Decision 9's scope | Same model across arms isolates architecture. |
| The tools agents' turn, tool-call and per-case cost budgets are set from the dev-sample runs, binding on at most 5% of cases. | §2 of the pre-registration ("never lowered"), stated as a deviation | The $2 per-case ceiling bounds nothing at $0.09 to $0.15 a case. |
| The credit date no longer orders the work. Credit runs happen when their prerequisites are met. | Decisions 9 and 13; §9 schedule | Maintainer's call. |
| The frozen suites are rebuilt after the key fixes of 2026-11-05, with ids and question text unchanged, and stored runs are re-graded. | §12 item 5's suite hashes | A key audit found keys naming a component measure, through stale namespace-expanded ranks. |

The cost tables of §6 and §9 predate these changes. The re-costed plan, from zero, is about
$110 to $186 for the final look and $40 to $56 for development, about $180 to $290 to
publication with a 20% contingency at Flex, or $245 to $380 if the gpt-6.1 lines run at
Standard. Every figure is [E] until the dev-sample runs measure the agents' cost per case.
That re-costing predates the 2026-11-06 change and still includes `html_rag`. Its dollar lines
were small (cents a case); what drops out with it is mostly time, the reranking that was the
critical path of §8.

## 1. Decisions

| # | Decision |
|---|---|
| 1 | **The looks run on gpt-6.1** (`openai-6.1` profile: `gpt-6-luna` small, `gpt-6.1-terra` medium, `gpt-6.1-sol` frontier), at OpenAI's **Flex** tier. `frontier_sec_tools` uses `gpt-6.1-sol`. |
| 2 | **DeepSeek for development:** XbrlRag's dev baseline, the fixes, the slices and the validation runs. Run off-peak. |
| 3 | **The bar is read on one profile, gpt-6.1.** `xbrlrag_full` also runs alone on the `anthropic` profile at the final look, as a "not a GPT quirk" line that decides nothing. |
| 4 | **H-F is looked at once, at the final milestone.** There is no held-out checkpoint. It is replaced by a fresh-filer check on the wave-2 development filers, run right after their ingest (§4). |
| 5 | **H-F has about 450 cases** over the same 24 filings: at least 150 in the line-1 kinds and at least 200 single-filer lookup + compute (§7). |
| 6 | **The run matrix of §3:** `naive_rag_filtered` runs on dev only, `long_context_resolved` reuses `oracle`'s answer where the filing matches, `long_context_multi` runs only where k ≥ 2 filings fit the window, H-T runs 3 arms and set B runs `xbrlrag_full` only. |
| 7 | **No repeats.** The run-to-run noise that bar line 3 needs is measured on validation (two extra gpt-6.1 runs of `xbrlrag_full`), never on H-F. |
| 8 | **Baselines never run on validation.** They get no fixes, so there is nothing for validation to check. `xbrlrag_full` keeps its validation runs. |
| 9 | **The Anthropic credit** ($50, expires 2026-11-15) pays for the dev baseline's baseline arms and an `anthropic`-profile `xbrlrag_full` dev + validation run (§9). XbrlRag's DeepSeek dev baseline stays as the before-number for the fixes. |
| 10 | **Judges:** `claude-haiku-5-5` in development (already in `configs/eval/judge.yaml`); `gpt-6-luna` as the milestone judge for the Anthropic arm, replacing `gpt-5.6-luna`. |
| 11 | **H-X stays at 75 questions** (§11). |
| 12 | **Every expensive run can resume after a restart:** spec `eval-run-checkpoint` lands before the first expensive run, the credit runs of §9, so it covers those, the gpt-6.1 checks and the final look (§10). |
| 13 | **The credit runs and everything they need (§9) are done by 2026-11-15.** The plan carries no fallback for missing that date. |

Why gpt-6.1: it lists at about half of gpt-5.6, with half the cache-read price again; it is
a newer generation, so less exposed to retirement mid-campaign; and gpt-5.6-sol's price is
promotional only "at least through November 21, 2026". The choice was made on price and model
generation, before any held-out measurement and with no comparison run, and Amendment 1 says
exactly that.

## 2. What this changes in the pre-registration

The merged pre-registration says, in §2, "All systems of the v2 plan §3.6 run at each look"
and "the decision runs use `openai-5.6`", with `frontier_sec_tools` on `gpt-5.6-sol`. §7 says
"Amendments may add detail. Once H-X writing has started, §3 and §4 are not changed."

- Decisions 1, 3 and 6 **change §2**; they are not detail. Amendment 1 carries a **stated
  deviations from §2** section, giving the reason for each change, and the results disclose it.
- The v2 plan (§7.3, §11) promised that milestones repeat on the `openai-5.6` and
  `anthropic` profiles. Decision 3 replaces that with a single `anthropic` line for
  `xbrlrag_full`, and is stated as a deviation too.
- **§3 and §4 are untouched.** In particular `long_context_oracle` runs where the bar is
  read, because line 3 needs it.
- §1 allows up to three looks; this plan uses one for H-F and H-X. Fewer looks is within §1.

## 3. The run matrix

Held-out columns run on gpt-6.1 at Flex; the Dev column runs on DeepSeek except the cells
paid by the credit.

| Arm | Bar line | H-F | H-X | H-T | Set B | Dev |
|---|---|---|---|---|---|---|
| `xbrlrag_full` | subject | yes | yes | yes | yes | full dev + validation; slices; validation after fixes |
| `frontier_sec_tools` | 1 | yes | yes | yes | no | 100-case sample (credit) |
| ~~`html_rag`~~ dropped (§0) | none | no | no | no | no | no |
| `long_context_oracle` | 3 | single-filer | single-filer | no | no | 100-case sample (credit) |
| ~~`long_context_resolved`~~ dropped (§0) | none | no | no | no | no | no |
| ~~`long_context_multi`~~ dropped (§0) | none | no | no | no | no | no |
| `closed_book` | none | yes | yes | yes | no | full dev (credit) |
| `naive_rag_filtered` | none | no | no | no | no | full dev once (credit) |
| `xbrlrag_full`, `anthropic` profile | none | yes (list price) | yes | no | no | dev + validation (credit) |

What each arm is for:

- **`long_context_oracle`** is the ceiling for "paste the 10-K": it is handed the right
  filing. It is the one comparator XbrlRag could lose to; dropping it would flatter XbrlRag
  and leave line 3 unevaluable.
- **`long_context_resolved`** is the realistic single paste, with the filing chosen from the
  question. Where the resolver picks the oracle's filing the call is byte-identical, so the
  oracle's answer is reused: the gap between the two arms is then exactly the resolver's
  effect, with no sampling noise added.
- **`long_context_multi`** pastes up to 10 filings into a 250K-token window. On a
  single-filing case it is `resolved` with a different wrapper, so it runs only where the
  resolver names two or more filings that fit. Its fit table (how many cases exceed the
  window) costs nothing and is the cheapest evidence for why pasting does not scale to
  cross-filer questions.
- **`closed_book`** is the memorisation control: no documents. A newer model is more likely to
  have FY2024–25 figures in its training data, and H-T is where its score should fall.
- **H-T** asks "could this be memorised?", which touches the model-only arm, the product and
  the live-API agent. `html_rag` on H-T would need an `html10k_text` load and a rerank per
  case for a line nobody reads.
- **Set B** asks one question: did v2 close the gap v1 exposed (75.2% out of loop against
  about 97% in loop)? `xbrlrag_full` answers it. A long-context rematch on B would add about
  $6 and is not planned.
- **`naive_rag_filtered`** answers "how bad is basic RAG?", which is not the claim;
  `html_rag` is the RAG comparator the bar uses. [M] Dropping it saves little time: three runs
  on 2026-10-28 took 12.2–12.4 minutes for 482 cases (median 7.0 s a case).

## 4. The fresh-filer check instead of a held-out checkpoint

The dev/validation split catches overfitting to *questions*: validation has its own templates
and phrasings. It cannot catch overfitting to *filers*, because both splits use the same 33
filers. Some fixes are filer-specific by nature, such as the concept-family overrides for
Vistra, Constellation and McDonald's. [M] External test v1 found exactly this kind of gap,
75.2% against about 97%.

A held-out checkpoint would measure that, but it spends a look and H-F's freshness, and it
can only be read as aggregates.

The wave-2 filers (XOM, V, TMUS, JNJ, PEP, BA, CAT, DIS) are development filers, excluded from
the held-out draw ([draw record](README.md)). They are ingested in phase 6, after the fixes and
before the final look. **Run the generated suite on them immediately after their ingest,
before any wave-2 override or alias is written.** At that moment nobody has tuned on them, and
the result can be read case by case, so it is more useful for fixing than a held-out
checkpoint. Cost: about $1 on DeepSeek.

## 5. Prices [V]

Sources, read 2026-11-03: the OpenAI pricing page (`developers.openai.com/api/docs/pricing`)
with its prompt-caching, flex-processing and deprecations guides; DeepSeek's
`api-docs.deepseek.com/quick_start/pricing`; Anthropic's pricing page.

USD per 1M tokens. "Write" is the default 30-minute cache write.

| Model | Input | Cached read | Write | Output | Flex / Batch |
|---|---|---|---|---|---|
| gpt-6.1-sol | 2.00 | 0.10 | 2.50 | 10.00 | all four halved |
| gpt-6.1-terra | 1.00 | 0.05 | 1.25 | 5.00 | halved |
| gpt-6-luna (no 6.1 luna exists) | 0.10 | 0.01 | 0.125 | 0.50 | halved |
| gpt-5.6-sol (promotional to at least 2026-11-21) | 4.00 | 0.40 | 5.00 | 20.00 | halved |
| deepseek-flash, peak / off-peak | 0.30 / 0.15 | 0.006 / 0.003 | none | 1.20 / 0.60 | |
| deepseek-v4-pro, peak / off-peak | 1.32 / 0.66 | 0.044 / 0.022 | none | 3.96 / 1.98 | |
| claude-haiku-5-5 (prompt ≤ 100K) | 0.10 | 0.01 | 0.125 | 0.50 | batch halved |
| claude-sonnet-5-5 | 2.00 | 0.10 | 2.50 | 10.00 | batch halved |
| claude-opus-5-5 | 4.00 | 0.20 | 5.00 | 20.00 | batch halved |

Facts that bear on the plan:

- **Long-context pricing** on gpt-6.1-sol (4 / 0.20 / 5 / 15) starts above 272K input tokens.
  The arms' window is 250K (`DEFAULT_MAX_INPUT_TOKENS`), so no look reaches it.
- **gpt-6.1 cache:** minimum prefix 1,024 tokens, lifetime 30 minutes. The 12-hour option
  (2.5× input to write) is not needed, because the shared schedule finishes a filing's cases
  inside 30 minutes.
- **DeepSeek peak** is 01:00–04:00 and 06:00–10:00 UTC, Monday to Friday, excluding Chinese
  public holidays; all other times, weekends included, are off-peak at half price.
  `pricing.yaml` tracks peak, so an off-peak bill is about half the tracked figure.
- **Haiku over 100K tokens pays 5×.** A filing is about 110K tokens, so long-context arms on
  the `anthropic` profile must run on Sonnet or Opus.
- **The gpt-5 family shuts down 2026-12-11.** The repo's default `openai` profile is gpt-5
  (`configs/llm/llm.yaml`), so any run that falls through to the default fails after that
  date. Retire or repoint it before then.

### Flex

OpenAI's Flex tier is requested with `service_tier: "flex"`. Tokens are priced at Batch rates
("ideal for ... model evaluations"), every token class halves, cache writes included, in
exchange for slower responses and occasional "resource unavailable" 429s that are not charged.
gpt-6.1-sol, gpt-6.1-terra and gpt-6-luna are all on it.

- Every look is offline, so latency doesn't matter and a 429 is a retry.
- Bar line 1's cost ratio is unchanged: both systems run on the same tier and the ratio is
  scale-free.
- **`pricing.yaml` stays at Standard.** The gateway records list price, the caps stay
  conservative, and the published per-question costs are list-price costs. One sentence
  discloses that the bill was at Flex rates.
- **Go/no-go:** the 10-case frontier smoke runs on Flex and records 429 count and latency. If
  Flex is unusable that day, the look runs Standard and the Standard column of §6 applies.

## 6. Cost [E]

Assumptions:

- A filing is about 110K tokens. [M] 51,806 html10k chunks over 123 filings is about 421
  chunks of about 1,000 characters each, so about 105K tokens a filing, plus 5% for the prompt.
- Output is 3K tokens a call, including reasoning. This is **not measured on gpt-6.1**; the
  gpt-6.1 smokes measure it before any cap is written.
- The frontier arm is shown at 3K–15K output a case, because a 20-step loop may well exceed
  3K.
- H-F is at 300 cases here, and §7 adds the cost of the extra 150. H-X has 75 cases.

**Final look, H-F + H-X, at 300 H-F cases**

| Arm | Standard | Flex | Basis |
|---|---|---|---|
| `long_context_oracle` | 31 | 15.5 | writes for 24 + ~40 filings $17.6, reads $3.0, output $10.2 |
| `long_context_resolved` | 3 | 1.5 | router calls plus the cases where the resolver differs; $14 / $7 without reuse |
| `long_context_multi`, k ≥ 2 only | 23–54 | 12–27 | assumes 25% of H-F needs two filings; pairs reused or not |
| `frontier_sec_tools` | 28–70 | 14–35 | 3K–15K output a case; re-cost from the smoke |
| `html_rag` | 4.3 | 2.1 | [M] pilot: 7,936 in, 696 out (incl. 255 reasoning) a call, on terra |
| `xbrlrag_full` | 2 | 1 | [M] $5.20 for 482 cases on openai-5.6 (2026-10-28), halved for gpt-6.1 |
| `closed_book` | 0.5 | 0.25 | |
| judge (Haiku) | 1 | 1 | not tiered |
| **H-F + H-X** | **93–166** | **47–83** | |
| H-T: 3 arms, ~100 cases | 10–20 | 5–10 | |
| Set B: `xbrlrag_full` | 0.3 | 0.2 | |
| `xbrlrag_full` on `anthropic` (list price) | 5 | 5 | |
| **Final look, H-F 300** | **108–191** | **57–98** | |
| Extra 150 H-F cases (§7) | 25–46 | 14–24 | |
| **Final look, H-F 450 (the plan)** | **133–237** | **71–122** | |

**To publication, at H-F 450**

| Stage | Model | Estimate |
|---|---|---|
| Pre-baseline smokes and suite rebuild | DeepSeek | 1 |
| Dev baseline: XbrlRag on DeepSeek (~$2); baseline arms and the `anthropic` line on the credit | DeepSeek + credit | 2 |
| Quarters (3b within 4b) | DeepSeek | 3–5 |
| Fixes: about 30 slices plus 2 validation runs | DeepSeek | 15–20 |
| Judge calibration | Haiku | under 1 |
| gpt-6.1 checks: smokes, `xbrlrag_full` validation ×2 | gpt-6.1 Flex | 5 |
| `eval-run-checkpoint`: live resume test, a few cases killed and resumed | DeepSeek | under 0.5 |
| Fresh-filer check on wave 2 | DeepSeek | 1 |
| Final look, H-F 450 | gpt-6.1 Flex | 71–122 |
| Subtotal | | 99–158 |
| Contingency, 20% (interruptions, errored runs) | | 20–32 |
| **Total to publication** | | **about $120–190** |

The credit pays a further $36–46 (§9), not counted above. If Flex is unusable on the day, the
final look runs at Standard ($133–237) and the total is about $195–325.
Infrastructure, electricity and ingest are excluded, as pre-registration §2 excludes them.

[M] The DeepSeek pilots behind the dev figures, on `deepseek-flash` at tracked peak price:
`html_rag` $0.1463 for 51 cases, `long_context_resolved` $0.1065 for 20, `frontier_sec_tools`
$0.161 for 20.

## 7. H-F size, by kind

The bar's power lives in the discordant pairs of its McNemar tests, which live in specific
kinds. A rough count, with XbrlRag at 85%:

- **Line 1** (pooled cross-filer, cross-period and compound) against a frontier arm at 65%:
  about 40% of pairs discordant, so 120 cases give about 48 pairs split about 36 to 12, which
  is clearly significant. Against a frontier arm at 75%, about 32% are discordant: 38 pairs
  split 25 to 13, p near 0.07. So 120 cases detect a 20-point gap and miss a 10-point one.
- **Line 3** is a point-estimate margin of 5 points. At 150 single-filer cases and 90%
  accuracy, one arm's standard error is 2.4 points and the difference's noise is about 3
  points, so a true 2-point gap could print as 6.

Hence about 450 cases: **at least 150 in the line-1 kinds and at least 200 single-filer
lookup + compute**, over the same 24 filings. More cases are cheap because the expensive
part, the filing cache writes, is paid per filing and the filings don't change.

Marginal cost of the extra 150 (100 single-filer, 25 cross-period, 25 cross-filer or
compound), Flex:

| Arm | A case | +150 |
|---|---|---|
| `frontier_sec_tools` | $0.04–0.10 single-filer, $0.06–0.15 cross-filer | $6.5–17 |
| `long_context_oracle` | $0.02, single-filer and cross-period | $2.6 |
| `long_context_resolved` | router call only, with reuse | $0.15 |
| `long_context_multi` | $0.026, cross-period only | $0.65 |
| `html_rag` | $0.007 | $1.0 |
| `xbrlrag_full` | $0.003 | $0.45 |
| `closed_book` | $0.001 | $0.15 |
| `xbrlrag_full` on `anthropic`, list | $0.014 | $2.1 |
| **Final look** | | **+$14–24** (Standard +$25–46) |

Time is the larger price: one rerank per filer a question names, so about 250–375 more
reranks, **+9–14 hours** of RagInference.

## 8. Constraints that are not dollars

**Reranking is the critical path.** Only `html_rag` reranks heavily. [M] A rerank takes about
130 s at 50 candidates on this CPU (measured 2026-11-01), one at a time. Rerank scores are
cached by question and candidate order (`data/cache/html10k_rerank.sqlite3`), so a re-run of
the same questions costs nothing.

| Run | Reranks | Time |
|---|---|---|
| [M] Full dev split, 635 cases (for reference; not planned) | 1,846 | ~67 h |
| [M] Full validation split, 265 cases (not planned) | 1,034 | ~37 h |
| [E] Dev sample, 100 cases | ~290 | ~10–11 h |
| [E] Final look, H-F 300 + H-X | ~1,090 | ~39 h |
| [E] Extra 150 H-F cases | 250–375 | +9–14 h |
| **[E] Final look, H-F 450 + H-X (the plan)** | **~1,340–1,465** | **~48–53 h** |
| [E] Loading the 24 held-out filings into `html10k_text` first (about 10K chunks at [M] 1.3 s a chunk) | | ~4 h |

At the final look, start `html_rag` first.

**Other constraints:**

- **DeepSeek off-peak:** schedule the dev baseline, the quarters run and the validation runs
  outside 01:00–04:00 and 06:00–10:00 UTC on weekdays, or at weekends.
- **Flex latency:** long-context calls can take minutes and the frontier loop ten. Use the usual
  worker count and expect the look to take a day, not an hour. The long-context stages need a
  longer request timeout than `LLMConfig.timeout`'s 60 seconds; OpenAI's guide recommends 15
  minutes.
- **Dates:** the credit expires 2026-11-15; gpt-5.6-sol's promotion ends at least 11-21 (no
  longer relevant once on gpt-6.1); the gpt-5 family dies 2026-12-11.

## 9. The Anthropic credit

The looks cannot run on the credit:

- **Timing.** Every look needs the fixes, the frozen H-F suite (the generator still lacks the
  compound shape the bar needs), H-X (not yet written) and, for H-T, filings not yet published.
  None of that lands by 2026-11-15.
- **Price.** Sonnet 5.5 lists at gpt-6.1-sol's rates, Opus 5.5 at twice. Only Anthropic Batch
  matches OpenAI Flex, the pipeline has no Anthropic batch path, and the frontier agent's
  multi-step loop does not batch well.

What it does buy before 11-15 [E]:

| Run on credit | Estimate |
|---|---|
| Long-context arms, 100-case dev sample, Sonnet | $10 |
| `frontier_sec_tools`, 100-case dev sample, Sonnet or Opus | $10–20 |
| `html_rag`, `closed_book`, `naive_rag_filtered` | $3 |
| `xbrlrag_full`, dev + validation, `anthropic` profile | $13 |
| **Total** | **$36–46** |

The baselines receive no fixes, so their dev numbers don't depend on the fix loop's model.
The `anthropic` dev run is the dev-side counterpart of the final look's `anthropic` line. It
also measures frontier-class tokens weeks before the gpt-6.1 smokes. Net saving is about
$6–8.

Rejected: Haiku as the development model (saves $3–5 for a plan change, a judge swap and a
re-smoke) and Anthropic as the decision profile (costs $50–90 more). Judge calibration,
paraphrases (a fourth model family by rule) and H-X (hand-written) cannot use the credit.

**Check first:** a credit-only account usually sits in the lowest usage tier. Read its
per-minute token limits in the console and set the shared rate limiter to them before the
first run.

**Before the credit runs:** the concept-key rollout, the dev-sample script, the arms' model
override and `eval-run-checkpoint` (§10), all inside the 12 days to 2026-11-15.

## 10. Surviving a restart during a two-day run

**Today**, results persist once, at the end, in one transaction. A power cut, a restart or a
`RunHalt` from the circuit breaker loses every graded result. `EvalRunner.run` accepts a
`prior` report and an `on_result` hook, but `run_suite.py` wires neither. The gateway retries
3 times with at most 8 s of backoff before a failure advances the breaker, which on Flex makes
one bad half-hour at OpenAI fatal. What survives: the rerank cache, and audit rows under
`--persist-audit`. What is lost: the arm's LLM spend ($15–35 for a long-context or frontier
arm) and in-flight agent loops.

**Spec `eval-run-checkpoint`** (locked-spec route: a wrong resume double-counts or skips cases
silently; about a day):

1. The run row is inserted at start as `RUNNING`; each case result is written on `on_result` in
   its own transaction; the row is finalised at the end.
2. `--resume RUN_ID`: stored results become `prior` (needs `CaseResult.from_dict`); same run
   id; refused unless the suite hash, system, model, profile, git sha and config all match;
   resume segments are recorded in config. In-flight cases have no row, so they re-run.
3. A `RunHalt` keeps the partial report.
4. `--with-system` siblings resume together.
5. Flex retries: many attempts, backoff into minutes, and a 429 "resource unavailable" is not a
   breaker failure; a 429 mid-loop resumes the frontier agent at the same step.
6. Errors are tagged as the arm's own or as transport; `--resume --retry-errors transport`
   redoes only the latter. Amendment 1 pre-declares this rule.

**What an interruption costs once this is in** [E], per interruption. Only the in-flight
cases are lost (workers × one case), plus one cache write per filing group in flight, since
a restart usually outlasts the 30-minute cache lifetime.

| Run | Dollars lost | Time lost |
|---|---|---|
| `long_context_*` (Flex) | in-flight cases' output plus one write per group, about $0.15 each: under $1 | the in-flight calls, minutes |
| `frontier_sec_tools` (Flex) | in-flight loops, $0.05–0.15 each: under $1 | up to 10 minutes a loop |
| `html_rag` | cents | in-flight reranks, about 130 s each; finished reranks are cached |
| `xbrlrag_full`, `closed_book` | cents | seconds |
| Every run | | the restart itself: services back up and the pre-spend checks repeated, about 15–30 minutes |

Without it, the same interruption loses the whole arm: up to $15–35 and up to a day of a
long-context or frontier arm, and the 20% contingency would not cover two such losses.

**Cost of building it:** about a day of work, offline unit and spec tests ($0), and one live
resume test on DeepSeek (under $0.50, in §6). It lands before the credit runs (decision 12),
so every expensive run in this plan can resume.

**The machine:** pause Windows Update and set active hours for the window; use a UPS. After a
restart, start RagInference by hand, confirm Postgres and the Qdrant container are back, and
repeat the pre-spend checks. Run the watchdog scoped by `--root-pid` or with a
`--max-age-seconds` of days, or it kills the look at 30 minutes.

## 11. Why H-X stays

Persona paraphrases fix v1's *wording* leakage. H-X guards the *question* axis, which
paraphrases cannot reach:

- **The intent space is ours.** Every generated question is kind × filer × metric × period ×
  operation from the committed lexicon, and the resolver is built from the same vocabulary. A
  paraphrase rewrites a generator intent and never asks what the generator cannot express.
- **Kinds the generator lacks.** Honesty and text shapes are missing (pre-registration §6,
  known gap) and compound is still being built. H-X carries 15 of each, the only held-out
  evidence for confident-wrong on false premises and for judge-graded text answers.
- **Three axes, three sets.** H-F closes filers, H-T time and H-X the question. Set B found the
  97-to-75 gap in v1 because its questions were not generator intents.
- **Separable misses.** On dev filers, an H-X miss is a wording or intent miss, never a
  coverage miss.

Cost at the final look: about $10–18 at Flex and under 3 hours of reranks. H-X is in the
merged pre-registration (§1, §4), so removing it would be a disclosed amendment.

## 12. What must exist before the first gpt-6.1 dollar

`eval-run-checkpoint` (§10) is already in place: it lands before the credit runs.

1. `configs/llm/pricing.yaml`: `gpt-6.1-sol` (2 / 10, read 0.10, write 2.50; above 272K
   4 / 15, read 0.20, write 5) and `gpt-6.1-terra` (1 / 5, read 0.05, write 1.25; above 272K
   2 / 7.50, read 0.10, write 2.50) rows, plus `cache_write` 0.125 on `gpt-6-luna`.
   `configs/llm/llm.yaml`: profile `openai-6.1` = luna / terra / sol. Retire or repoint the
   default `openai` profile before 2026-12-11.
2. `service_tier` per run (env or `run_suite.py` flag) passed by the OpenAI adapter and
   recorded with the run; the longer timeout for the long-context stages; 429 handling (§10.5).
3. `configs/eval/judge.yaml`: milestone judge `gpt-6-luna`.
4. The shared oracle/resolved schedule (PR 246) gains the answer reuse and the k ≥ 2
   restriction of §3.
5. Amendment 1: the matrix of §3, the stated deviations of §2, H-F's size by kind, no repeats,
   the per-arm caps (1.5 × the smoke's measured mean × cases), the error-retry rule, the
   suite hashes and the dev-sample hash.

**The gpt-6.1 smokes** (5 cases per long-context arm, which share filings; 10 for frontier, on
Flex) are the only measurement of gpt-6.1 output tokens, which drive every long-context and
frontier line. The first runs the day the profile lands, not after the fixes.

## 13. Still open

- **Flex usability:** settled by the frontier smoke.
- **gpt-6.1 output tokens:** settled by the smokes; every [E] figure for long-context and frontier
  is re-costed from them.
- **The credit account's rate limits:** read before the first Anthropic run.
- **A long-context rematch on set B** (about $6): not planned; a choice, not a need.
