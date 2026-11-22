# Unattended-run policy

This policy covers agentic, dev and scheduled runs that execute without a person watching:
what may run headless, with which caps, where alerts go, and how to roll back. It is the
dev-time counterpart of the product's Prometheus alerting
([`configs/monitoring/`](../../configs/monitoring/README.md)). Its enforcement arm is the
runaway-process watchdog, [`scripts/harness/watchdog.py`](../../scripts/harness/watchdog.py).
Read it before scheduling a long or LLM-calling job.

**In this doc:** [What may run](#1-what-may-run-unattended) · [Caps](#2-required-caps-for-any-llmcompute-run) ·
[Watchdog](#3-the-runaway-process-watchdog) · [Alerts](#4-alert-routing) ·
[Rollback](#5-confirmed-rollback) · [Kill switches](#6-kill-switch-inventory)

## 1. What may run unattended

A headless run is allowed only if it is read-only or gated (the ingest publish gate), runs
under the caps in §2, and is covered by the watchdog in §3.

| Run | Unattended? | Conditions |
|-----|-------------|------------|
| `pytest test/unit` (Stop gate, local CI) | yes | Read-only, bounded |
| `scripts/harness/check_features.py --gate` | yes | Read-only |
| `scripts/eval/run_drift_check.py`, `scripts/eval/run_regression_matrix.py` | yes, with caps | They call LLMs, so the §2 cost caps are mandatory |
| `scripts/harness/watchdog.py` | yes | Dry-run is read-only; `--kill` only ends in-scope runaways |
| `GET /metrics`, observability reads | yes | Read-only |
| Ingest dry-run (`--dry-run`) | yes | No writes |
| Ingest write / promotion ([`promotion.py`](../../src/ingest/sec_edgar/xbrl/promotion.py), publish gate) | human in the loop | Mutates the served fact store: run attended, or with `XBRLRAG_PUBLISH_GATE_B_ENFORCE=1` and a reviewed manifest |
| Anything touching `.env` or secrets | never | Blocked by the PreToolUse guard ([hooks](../../.claude/hooks/README.md)) |

Paid eval runs follow the pre-run checks in the root [`CLAUDE.md`](../../CLAUDE.md) as well.

## 2. Required caps for any LLM/compute run

Every unattended LLM run sets a hard ceiling; cost telemetry alone is not a cap. If a run has
no natural cost ceiling, it does not run unattended.

| Control | Setting | Source |
|---------|---------|--------|
| Per-run cost ceiling + circuit breaker | `XBRLRAG_RUN_MAX_COST_USD`, `XBRLRAG_RUN_MAX_LLM_CALLS`, `XBRLRAG_RUN_MAX_TOKENS`, `XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES` | [`src/llm/run_budget.py`](../../src/llm/run_budget.py) |
| Provider rate limits | `XBRLRAG_LLM_RPM`, `XBRLRAG_LLM_TPM` | [`src/llm/xbrl_gateway.py`](../../src/llm/xbrl_gateway.py) |
| Wall-clock / memory runaway | watchdog `--max-age-seconds`, `--max-rss-mb` | §3 |
| Semantic query cache (cost) | `XBRLRAG_QUERY_CACHE`, `XBRLRAG_QUERY_CACHE_L2` | [`src/pipelines/xbrl/query_cache.py`](../../src/pipelines/xbrl/query_cache.py) |

`scripts/eval/run_suite.py --max-cost-usd` is checked between cases, so it cannot stop a case
already running; size it with one case's cost as headroom.

## 3. The runaway-process watchdog

The watchdog detects, and on request kills, XbrlRag processes gone runaway: processes older
than a wall-clock budget, processes over a memory limit, and orphans whose parent has died.

- **Scoped.** It considers only processes whose command line matches the XbrlRag signature
  (`-m src.`, `src/` paths, `pytest`, `uvicorn src.api`, the retrieval pipeline, `sec_edgar`,
  the MCP server, `scripts/xbrl|eval|harness`; override with `--match`), or, with
  `--root-pid`, only the descendants of that PID. It never flags an arbitrary system process.
- **Dry-run by default.** It reports only; killing needs `--kill`, which terminates and then
  force-kills after `--grace-seconds` (default 5). It never flags itself, its parent, its whole
  ancestor chain (the launching shell or agent), PIDs 0 and 4, or PIDs given to `--protect`.
- **Defaults.** `--max-age-seconds` is 1800 (0 flags every in-scope process); `--max-rss-mb`
  is off unless set; orphans are flagged unless `--no-orphans`.

```powershell
# Report only: flag XbrlRag processes older than 30 min or over 8 GB
conda run -n py312 python scripts/harness/watchdog.py --max-age-seconds 1800 --max-rss-mb 8000

# Enforce: terminate the flagged processes and log each run as a JSON line
conda run -n py312 python scripts/harness/watchdog.py --max-age-seconds 1800 --kill --log-file logs/watchdog.jsonl

# Scope to one run's process tree instead of the signature
conda run -n py312 python scripts/harness/watchdog.py --root-pid <PID> --kill
```

The exit code is `1` when any runaway is found or killed, so a scheduler can alert, and `0`
otherwise; `--json` prints the run record. The live collector needs `psutil`. Without it, the
same clean-up by hand on Windows:

```powershell
Get-CimInstance Win32_Process -Filter "ParentProcessId=<PID>" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**Scheduling:** run the watchdog every ~10 minutes beside any long unattended job (Task
Scheduler, cron or the `schedule` skill), in dry-run first to calibrate the thresholds, then
with `--kill`.

## 4. Alert routing

| Signal | Goes to |
|--------|---------|
| Product latency, cost, errors, degradation | Prometheus alerts, [`configs/monitoring/alerts.yml`](../../configs/monitoring/alerts.yml) |
| Dev/agentic runaway processes | watchdog `--log-file` (JSON lines) + non-zero exit to the scheduler |
| Red build on a code change | Stop gate + local CI ([`scripts/ci/local_ci.py`](../../scripts/ci/local_ci.py)); workflows in `.github/workflows/` |
| Eval drift or regression | `run_drift_check.py` and `run_regression_matrix.py` (non-zero exit) |
| Per-run overspend | the run-budget circuit breaker raises in-process; visible in the trace and audit |

## 5. Confirmed rollback

"Confirmed" means: after rolling back, re-run the relevant gate and see it green.

| Change | Roll back | Confirm |
|--------|-----------|---------|
| Bad code | `git revert <sha>` (never force-push shared history) | `pytest test/unit` green; `check_features.py --gate` exits 0 |
| Bad ingest / publish | Re-run promotion to demote the accession in `publish_manifest`; the read-time deny-list stops it serving | Query the fact store: the bad filing is no longer returned |
| Stale query cache after a data fix | Bump the cache epoch (`query_cache_epoch`) | The next query misses the cache and recomputes |
| Runaway process | watchdog `--kill` (or the PowerShell fallback) | A watchdog re-run flags 0 |
| Bad config or hook change | Revert `.claude/settings.json` / `.claude/hooks/*` | Open `/hooks` to reload; guard and fixer behave as before |

## 6. Kill-switch inventory

Every automated control can be disabled on its own. Unattended runs turn caps on, not off:
disabling a cap for one needs a human decision recorded in the run's context.

| Switch | Effect |
|--------|--------|
| `XBRLRAG_STOP_GATE=0` | Disables the red-build Stop gate |
| `disableAllHooks: true` (settings.json) | Disables every dev-time hook |
| watchdog without `--kill` | Detection only, no termination |
| `XBRLRAG_RUN_MAX_*` unset | No per-run cost ceiling (not for unattended runs) |
| `XBRLRAG_LLM_RPM` / `XBRLRAG_LLM_TPM` unset | No client-side rate limiting |
| `XBRLRAG_QUERY_CACHE` unset or `0` (default) | The semantic cache is off |
| `XBRLRAG_PUBLISH_GATE_B_ENFORCE=0` (default) | The publish gate is advisory instead of blocking |
