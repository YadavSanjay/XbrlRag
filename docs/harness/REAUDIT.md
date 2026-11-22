# Harness re-audit cadence

<!-- reaudit:last=2026-11-14 -->
<!-- reaudit:next_due=2027-02-12 -->
<!-- reaudit:cadence_days=90 -->

Every harness component encodes an assumption of the form "the model can't do X": an
escalation ladder, a reasoning-effort cap, a verifier, a query decomposer. Those assumptions go
stale as models improve. This page sets a dated cadence for re-checking each one against
evidence and retiring the scaffolding the current models no longer need, and records the
latest audit. Read it before a re-audit, or before adding or removing a harness component.

The HTML comments above are machine markers.
[`scripts/harness/check_reaudit.py`](../../scripts/harness/check_reaudit.py) reads them,
reports `OK`, `DUE-SOON` (within 14 days) or `OVERDUE`, and with `--gate` exits 1 once
`next_due` has passed (`--strict` also fails on `DUE-SOON`). It warns when `next_due` is not
`last + cadence_days`.

**In this doc:** [Cadence](#cadence) · [Dimension scores](#dimension-scores-rev-2026-10) ·
[Component register](#component-register--the-staleable-assumptions) ·
[Procedure](#procedure-per-re-audit) · [Audit log](#audit-log)

## Cadence

- **Event-driven:** re-audit within 2 weeks of a model change in the query pipeline (a change
  of the default LLM profile or of a tier's model in
  [`configs/llm/llm.yaml`](../../configs/llm/llm.yaml)) and of any major model release from a
  provider in use.
- **Periodic floor:** at least every 90 days.
- **Standard:** the `ai-harness-engineering` skill's dated standard, currently rev 2026-10 (ten
  dimensions). Re-audit when the standard gets a new revision.

| | Date |
|---|---|
| Last re-audit | **2026-11-14** (rev 2026-10; evidence, scoring and prices only) |
| Next re-audit due | **2027-02-12**, or within 2 weeks of a model change, whichever is first |

## Dimension scores (rev 2026-10)

Scores and evidence as of the last re-audit (2026-11-14).

| # | Dimension | Score | Evidence | Top gap |
|---|---|---|---|---|
| 1 | Context & memory | Partial | Module `CLAUDE.md` files, `.claude/rules/`, project memory, handover files | Root `CLAUDE.md` is 340 lines (target ~200); `src/retriever/xbrl/CLAUDE.md` 308, `src/ingest/sec_edgar/CLAUDE.md` 221 |
| 2 | Planning | Solid | `VISION.md`, feature manifest + gate, tdd-spec specs, the v2 pre-registration and amendments | — |
| 3 | Execution | Solid | Read-only explorer and worktree-isolated worker subagents; parallel agents over disjoint files | — |
| 4 | Evaluation | Solid | v2 evaluation: SEC-derived keys, held-out sets, text judge under human-label calibration; v1 suite with noise band and slices; Stop gate, evaluator subagent, runtime verifier with a deterministic citation check | Verifier false-drop rate still unmeasured; `test/integration` never runs in CI |
| 5 | Guardrails | Partial | Guard (fails closed, symlink-aware), fixer, Stop gate; block/allow tests in `test/unit/test_hooks.py` pass | GitHub Actions minutes ran out (2026-11): PRs get no checks, and the CI gates run only through the local watcher `scripts/ci/local_ci.py`. Accepted by the maintainer while the repo is private (2026-11-14); revisit before it goes public |
| 6 | Containment | Partial | Read-only GitHub token, passphrase-gated push, deny rule on AWS `run_script` | Native Windows has no Bash sandbox; `.env` lives in the agent's workspace |
| 7 | Prompt & tool hygiene | Solid | Prompt registry, gateway adapters, fakes, schema validation | — |
| 8 | Cost | Partial | `RunBudget`, rate limiter, priced telemetry, cost-report skill; every model in use is priced; 30-day pipeline spend $120.68 | Price rows that differ from the provider pages (see the register); `--max-cost-usd` is checked only between cases |
| 9 | Autonomy safety | Solid | Watchdog, [UNATTENDED_RUNS.md](UNATTENDED_RUNS.md), full traces, git workflow | — |
| 10 | Evolution & measurement | Partial | This file, `check_reaudit.py` | The model-change re-audit ran 24 days after its trigger (due 2026-11-04); hook usage is not measured |

## Component register — the staleable assumptions

Each row is a harness piece and the assumption it encodes. On a re-audit, test whether the
current models still need it, with evidence.

Evidence is from `audit.query_event` / `audit.query_audit` on `xbrl_db` over the 30 days to
2026-11-14 (12,978 queries, 51,760 LLM calls) unless stated, and from the providers' pricing
and deprecation pages read on 2026-11-14. Verdicts that change the system wait until after the
v2 final held-out look, so the frozen system is what gets measured.

| Component | Encoded assumption ("the model can't…") | Retire when… | Evidence (2026-11-14) | Verdict |
|-----------|------------------------------------------|--------------|------------------------|---------|
| Model tiers + pricing ([`src/llm/profiles.py`](../../src/llm/profiles.py), [`configs/llm/pricing.yaml`](../../configs/llm/pricing.yaml)) | …be trusted to pick the right cost/quality tier itself | Prices/tiers are wrong or a cheaper tier matches quality | Migration done: default `openai-6.1` since 2026-10-21; the gpt-5 models were last called 2026-10-28. The `openai` profile still maps to gpt-5 models that shut down 2026-12-11; gpt-4o snapshots shut down 2027-01-14. Rows that differ from the pages: `gpt-6-luna`, `gpt-5.6-terra`, `gpt-5.6-luna` lack their >272K long-context tier (cost understated above it); `gpt-6-sol` lacks its cache-write rate and tier; gpt-5.6-sol's promotional price runs to 2026-11-30, not "at least 2026-11-21". The rest match | **Fix** the rows; retire or repoint the `openai` profile before 2026-12-11 (decision) |
| `cached_input` multipliers | …provider cache-read discounts are as noted | Provider pricing changes | OpenAI and DeepSeek rows match. Anthropic: `claude-sonnet-5-5` cache reads are $0.10/M (0.05× input), but the row has 0.0002/K (0.1×), so tracked cost is overstated; the row comment says 0.1× for all but Opus | **Fix** the Sonnet row and its comment (decision: it changes the Anthropic arm's tracked cost) |
| `reasoning_effort="low"` bounds (router / verifier / decomposer) | …handle these calls without burning the token budget on reasoning | A model self-bounds and stops returning empty content | Empty replies only on DeepSeek: verifier 135 of 3,256 calls (4.1%), synthesizer 62 of 3,373, through 2026-11-11. None since the output budget was sized from each model's ceiling (`fix/synthesizer-output-cap`, 2026-11-11; about 1,900 calls since). None on the OpenAI 6.x or Claude 5.5 models | **Keep** |
| Model-escalation ladder ([`escalation.py`](../../src/llm/escalation.py)) | …a cheaper model won't deteriorate | Escalations rarely fire | It now fires: 134 escalations (2026-11-04 to 11-11), 130 of them the DeepSeek synthesizer (`parse_error` 68, `empty_content` 62), 2 the Claude verifier, 2 the planner. The rung failed: `deepseek-v4-pro` came back empty on 115 of its 130 synthesizer calls. None since `fix/synthesizer-output-cap` | **Keep**, no longer a removal candidate; after the final look, test whether an escalation recovers an answer on the decision profile |
| Runtime faithfulness verifier | …the synthesizer won't make unsupported statements | Unsupported-statement rate ≈ 0 | Dropped ≥1 statement in 1,409 of 9,672 answers it checked (14.6%; 35% at the last audit). The deterministic citation check dropped 464 before the LLM (423 number mismatch, 41 unresolved ids). Did not complete on 155 answers (135 empty DeepSeek replies, 20 parse errors) | **Keep**; measuring how many dropped statements were correct is still open |
| Query decomposer | …answer a compound query without splitting | A model answers compound queries directly at equal accuracy | Split 257 of 12,978 queries (2.0%) | **Test** (still open): an eval slice with decomposition off, after the final look |
| Evaluator subagent + Stop gate | …a generator can grade its own change / won't stop on a red build | Never: a process guarantee | Stop gate tested (`test_hooks.py`, passing 2026-11-14) | Keep |
| Protected-path guard | …an agent won't touch secret files | Never: a security control | Fails closed, symlink-aware, tested both ways | Keep |
| Runaway-process watchdog | …agentic runs never leak zombie children or retry loops | Never: environmental | `test_watchdog.py` | Keep |

**Rule of thumb:** capability scaffolding (tiers, effort caps, escalation, decomposition) is a
candidate for removal as models improve; process and security guarantees (doer-not-judge, Stop
gate, guard, watchdog) stay regardless of model.

## Procedure (per re-audit)

1. **Gather evidence** for every register row: `audit.v_escalations` and `audit.query_event`
   (escalations, retries, empty content, verifier drops, decomposer splits), the latest eval
   runs at `/tech/eval`, and the providers' pricing and deprecation pages.
2. **Score the ten dimensions** against the current standard and update the table.
3. **Per register row**, decide keep / test / retire from the evidence, not intuition. Remove
   one component at a time, each validated by an eval slice.
4. **Re-verify prices and model lifecycles** in `configs/llm/pricing.yaml`.
5. **Update the markers:** `reaudit:last` to today, `reaudit:next_due` to today +
   `cadence_days`, and add a line to the audit log.
6. Run `conda run -n py312 python scripts/harness/check_reaudit.py`; it should report `OK`.

## Audit log

| Date | Standard rev | Outcome |
|------|--------------|---------|
| 2026-11-14 | rev 2026-10 | Event-driven re-audit for the default-profile change (steps 1, 2, 4, 5 only; no component changed). Model migration closed. The escalation ladder now fires, but on DeepSeek its rung mostly failed until `fix/synthesizer-output-cap`; it is no longer a removal candidate. Verifier drop rate down to 14.6%. Price rows that differ from the provider pages listed for a fix; the `openai` profile's gpt-5 models shut down 2026-12-11. Guardrails scored Partial while PR checks run only through the local CI watcher. Behaviour changes wait for the v2 final look. |
| 2026-10-17 | rev 2026-10 | Ten-dimension re-audit. Opened the model migration for the 2026-12-11 shutdown; escalation ladder marked a removal candidate; verifier and reasoning caps kept; decomposer queued for an A/B slice; guard hardened (fails closed, symlink-aware, covers NotebookEdit and Read). |
| 2026-07-14 | rev 2026-07 | First dated pass; cadence and register established; nothing retired. |
