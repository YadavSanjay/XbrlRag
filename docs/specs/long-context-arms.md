# long-context-arms — the whole filing, without the oracle

`long_context` today is handed the right filing: it reads `case.ticker` and `case.fy`, which
are the answer key's own fields. That makes it a ceiling, not a competitor. This change adds
the two arms of [v2 plan](../design/XbrlRag_v2_Plan.md) sections 3.6 and 5.5, and gives the
existing arm the name that says what it is.

| Arm | Filing | Status |
|---|---|---|
| `long_context_oracle` | the case's own, from `case.ticker` and `case.fy` | today's `long_context`, renamed; behaviour unchanged |
| `long_context_resolved` | one, chosen from the question by XbrlRag's own resolver | new |
| `long_context_multi` | the k filings that resolver names, concatenated | new |

No XbrlRag pipeline behaviour changes.

## Requirement

### The resolver

1. **Same resolver as XbrlRag.** Both new arms call `IntentRouter.route(case.question)`, with a
   real `EntityExtractor`, built as `XbrlRetrievalPipeline` builds them (`known_tickers` empty,
   the stage's model from the active profile). That is one LLM call on stage `intent_router`,
   then SQL. The arms read `companies` and `periods` from the result and use nothing else from
   it: not `intent`, not `plan`, and never `case.ticker`, `case.fy` or `case.cik` to choose a
   filing. `QueryDecomposer` is not run (see Notes).
2. **What XbrlRag does when the resolver cannot choose.** `BranchDispatcher._unresolved_reasons`
   finds a reason when there is no company, any company is ambiguous, or any period is ambiguous.
   XbrlRag then does **not** guess a filer or a year and does not stop. It takes the degraded
   `NARRATIVE_BRANCH`: a hybrid chunk search filtered only by what did resolve, then synthesis
   and the verifier, with no fact-store lookup. A whole-filing arm has no search, so its
   equivalent is: **no guess, no filing, no answer call.** On any of those three reasons the arm
   returns `SystemAnswer(refused=True, text=<the reason>)`, `error=None`. It is a refusal, not an
   exclusion, so the case stays in the denominator and a resolver error costs the arm as it costs
   XbrlRag. The cost of the router call stays on the answer.
3. **`filing_targets(result)`** is the pure rule that turns a router result into filings to fetch:
   - It returns `([], reason)` on any reason of requirement 2, and when a resolved company has no
     ticker.
   - Companies are the distinct resolved tickers.
   - With at least one period, years are every `p.fiscal_year`, plus every year written in a
     period mention (`extract_years_in_range(mention, [2018, 2035])`, the extractor's own helper
     and range: the extractor keeps only the latest year of `"FY2021 to FY2023"`). Targets are
     the product of companies and years.
   - With no period mention, each company gets its own `last_seen_fy`, the value XbrlRag's
     planner is given. A company whose `last_seen_fy` is `None` gives `([], reason)`.
   - Targets are sorted by `(ticker, fiscal_year)`, without duplicates, and the reason is `None`.

### `long_context_resolved`

4. **One filing.** The arm needs one company among `filing_targets`. More than one is a reason as
   in requirement 2: it refuses. With several years it takes the latest, whose comparative columns
   carry the earlier ones. This is the rule the oracle arm follows for a year-over-year case.
5. **Same filing path as the oracle.** The key comes from `FilingResolver.canonical_key(ticker,
   fiscal_year)`, the text from `FilingStore` and `filing_text`. The call is the oracle's: stage
   `long_context`, the same prompt and version, one system message, a cache-point filing message,
   a question message, serialized per filing, parsed with `parse_structured_answer`,
   `fact_ids == []` and `chunk_ids == []`. The arms differ only in which filing was read.
6. **The corrected fiscal-year label.** The fiscal year goes to `canonical_key` as the resolver
   gave it, and the key comes back from the resolver. The arm never derives a key from a ticker
   and year: since [fiscal-label-alignment](fiscal-label-alignment.md) the label and the path's
   year differ. Home Depot's fiscal 2024 is the filing ending 2025-02-02, at
   `HD/2025/10-K/canonical.htm`; `PostgresFilingResolver` selects on
   `filing_registry.fiscal_year`, which carries the label.
7. **Exclusions come from the case, never from the resolver.** The arm returns the oracle's
   `"excluded: "` error, before any LLM call, when `case.ticker` or `case.fy` is missing. So both
   arms are scored on the same cases. Those two fields are read for this check only.
8. **A resolved filing that does not exist is a refusal.** When `canonical_key` returns `None`,
   the resolver's year may well be the error, so the arm refuses (status `no_filing`), with no
   answer call. A key that is missing from the store, or text over the limit, is a fact about the
   corpus: `"excluded: "`, as in the oracle arm.

### `long_context_multi`

9. **The k filings.** The arm fetches every target of `filing_targets`, in that order, and sends
   them in one call on its own stage `long_context_multi` (a versioned prompt, `v1`, which says it
   is given several filings). The filing message is the cache point and holds a `json.dumps` list,
   with `ensure_ascii=False`, of `{"ticker": ..., "fiscal_year": ..., "text": ...}`, in that key
   order and in target order. So the same filings give the same prefix whatever order the
   question names them in. The question goes in its own message. Calls on one set of filings are
   serialized; different sets run concurrently.
10. **Exclusions.** A case of kind `screen`, or with a `universe`, ranges over filers the question
    does not name: `"excluded: "` before any LLM call. `case.ticker` and `case.fy` are not read.
11. **Filings that are missing.** A target whose `canonical_key` is `None`, or whose key is not in
    the store, is left out and listed in `missing`, and the call goes ahead on the rest: XbrlRag's
    plan steps also fail one at a time, and the prompt says to refuse what the text doesn't hold.
    When no filing is left: refusal if no key resolved (`no_filing`), `"excluded: "` if a key
    resolved but no store object exists (`not_in_store`).
12. **Fit is reported, never truncated.** The total is the sum of `len(filing_text(...))` over the
    filings, and the limit is `4 * max_input_tokens` characters. Over the limit, the arm makes no
    answer call and returns `"excluded: "`, with status `over_window`. No filing is dropped or
    shortened to make the rest fit. `fits` records where they stopped fitting: the largest `m`
    such that the first `m` filings, in target order, total at most the limit.
13. **A bound.** More targets than `max_filings` (default 10) is excluded with status `too_many`,
    before any `canonical_key` call or store read. `k` is then the number of targets.

### Both arms

14. **The record.** Every answer that reached the resolver carries `selection`, a JSON-safe dict,
    persisted through `SystemAnswer.to_dict()` under the key `"selection"` (the `produced` column):
    - `status`: `"sent"`, `"undecided"`, `"no_filing"`, `"not_in_store"`, `"over_window"` or
      `"too_many"`;
    - `reason`: why, or `None` when sent;
    - `filings`: `[{"ticker", "fiscal_year", "key", "chars"}]` in target order;
    - `missing`: `[{"ticker", "fiscal_year"}]`;
    - `k`: the length of `filings`, or the number of targets for `too_many`;
    - `total_chars`, `limit_chars`, `fits` once text was read.

    `to_dict()` is unchanged for an answer with no selection, so no other arm's payload moves.
    A run's table comes from `fit_summary(selections)`: for each `k`, how many were `sent` and how
    many did not fit (`over_window` and `too_many`). Other statuses are not fit results.
15. **Window limit.** `max_input_tokens` defaults to 250,000, as in the oracle, and a larger value
    raises `ValueError` at construction. `pricing.yaml` has no long-context tier for gpt-5.6-sol,
    which bills prompts over 272K tokens at 2x input and 1.5x output, so a larger window would
    record less than the provider charges and the run's cost cap would not hold (see Notes).
16. **Accounting and failures** work as for the other arms, through `answer_in_trace`:
    `query_id`, `latency_ms`, and `cost_usd` covering the router and the answer calls; an
    exception from the router, filing resolver, store or provider becomes `error`, not starting
    with `"excluded: "`; `RunHalt` propagates, from the router call as from the answer call. The
    default router, resolver, store and gateway are built on first use.
17. **Names.**
    - `system_names()` is `("xbrlrag_full", "closed_book", "naive_rag_plain",
      "naive_rag_filtered", "long_context_oracle", "long_context_resolved",
      "long_context_multi")`.
    - `LongContextSystem.name` is `"long_context_oracle"`. Its stage stays `long_context`, its
      prompt stays `v2`, and its behaviour is otherwise unchanged.
    - `build_system("long_context")` still works and builds that arm, so an old command runs. A
      new run is therefore never stored under the old name.
    - Persisted rows are not rewritten. `stored_system_names(name)` gives every name a system's
      rows can carry: `("long_context_oracle", "long_context")` for the oracle arm (either name
      given), `(name,)` for the rest. `run_suite.py --rerun-failed last` uses it, so it finds a
      `long_context` run.
    - `build_system(name, model=m)` takes a model for the three arms and for `"long_context"`, and
      raises `ValueError` for any other system.

**In scope:** requirements 1-17. **Out of scope:** `html_rag`, `frontier_sec_tools`, any paid run,
any change to the resolver, and any rewrite of persisted rows or sealed documents.

## Acceptance criteria

The fakes are the I/O boundaries only: a provider adapter behind a real `LLMGateway` (router and
answer each), an `EntityExtractor` whose connection is fake, a `FilingResolver` subclass or a real
`PostgresFilingResolver` on a fake connection, and an in-memory `FilingStore`. The arms,
`IntentRouter`, `EntityExtractor`'s logic, `filing_text`, the prompt registry and the answer
parser are real.

- **C1**: `system_names()` is the literal tuple. `build_system` builds each new name offline, with
  that `name`, and passes `model` through. `build_system("long_context")` is named
  `long_context_oracle`. `stored_system_names` is as stated. `--rerun-failed last` queries both
  names. The oracle arm keeps stage `long_context` and prompt `v2`. Oracle: requirement 17.
- **C2**: `filing_targets` gives the hand-listed result for one and two companies, one and several
  years, a range mention, no period, and each unresolved reason. Property: it equals the sorted
  product computed in the test, whatever the mention order. Oracle: requirements 2-3.
- **C3**: `long_context_resolved` routes `case.question`, sends the chosen filing's whole text on
  stage `long_context` and the oracle's prompt, and records the choice. Several years take the
  latest. Property: changing `case.ticker`, `case.fy` or `case.cik` changes nothing sent. Oracle:
  requirements 1, 4, 5.
- **C4**: with a real `PostgresFilingResolver` on a fake registry, Home Depot's fiscal 2023, 2024
  and 2025 read the filings ending 2024-01-28, 2025-02-02 and 2026-02-01, whose paths say 2024,
  2025, 2026. "Most recent" follows the extractor's `last_seen_fy`. Property: a key is used
  verbatim, whatever its path says. Oracle: requirement 6; Home Depot's own labels as read from
  the registry; [fiscal-label-alignment](fiscal-label-alignment.md).
- **C5**: each cannot-choose path gives `refused=True`, `error=None`, no answer call, a `selection`
  with status `undecided` or `no_filing`, and a cost equal to the router call. A case with no
  ticker or fy is excluded before the router. A store miss and an oversized text are excluded.
  Property: any mix of resolved and unresolved mentions with one unresolved refuses. Oracle:
  requirements 2, 7, 8.
- **C6**: `long_context_multi` sends the k filings in target order, in the stated format, under one
  cache point, on stage `long_context_multi` at `v1`; a screen or universe case is excluded before
  the router; a missing filing is left out and listed; none left gives the stated result.
  Property: mention order never changes the filing message. Oracle: requirements 9-11.
- **C7**: at the limit exactly the call is made; one character over it no call is made, nothing is
  truncated, and `fits` is as computed by hand. `max_filings` stops before any read. `fit_summary`
  gives the hand-built table. `max_input_tokens` over 250,000 raises `ValueError`. Property: `fits`
  and the decision equal the prefix sum computed in the test. Oracle: requirements 12, 13, 15.
- **C8**: `cost_usd` equals the router's and the answer's token costs at `MODEL_PRICING` rates;
  doubling tokens doubles it; `query_id` differs between answers and `latency_ms >= 0`; failures
  become errors; `RunHalt` propagates from both calls; calls on one filing set never overlap and
  different sets can. Oracle: requirements 5, 9, 16.
- **C9**: `to_dict()["selection"]` holds the stated record, survives `json.dumps`, and is absent
  for an answer with no selection. Property: `filings` lists exactly what was sent, in order.
  Oracle: requirement 14.

## Interface

New (stubbed in the spec PR), in `src/eval/systems/long_context_arms.py`:

- `filing_targets(result: IntentRouterResult) -> tuple[list[tuple[str, int]], str | None]`
- `fit_summary(selections: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, int]]`, whose values
  are `{"sent": int, "over_window": int}`
- `LongContextResolvedSystem`
  - `__init__(self, *, model: str = "gpt-5.6-sol", gateway: LLMGateway | None = None, router:
    IntentRouter | None = None, resolver: FilingResolver | None = None, store: FilingStore | None =
    None, max_input_tokens: int = 250000) -> None`
  - `answer(self, case: EvalCase) -> SystemAnswer`
  - its `name` is `"long_context_resolved"`, and it has a `model` attribute.
- `LongContextMultiSystem`, the same plus `max_filings: int = 10`; its `name` is
  `"long_context_multi"`.

In `src/eval/systems/__init__.py`:

- `stored_system_names(name: str) -> tuple[str, ...]`

Behaviour on existing code, which the implementer adds:

- `SystemAnswer` gains `selection: dict[str, Any]`, empty by default, and `to_dict()` includes it
  only when not empty.
- `LongContextSystem.name`, `system_names()`, `build_system()` and the package docstring.
- `prompts/templates/xbrl/registry.yaml` gains stage `long_context_multi`, with `v1` templates
  under `_base/long_context_multi/v1/` that keep the `<<cache point>>` line. It has no
  `XBRL_STAGE_TIERS` entry, because the model is explicit. `long_context` stays at `v2`.
- `run_suite.py`: `--system` also accepts `long_context`; `--model` applies to all four long-context
  names; `_failed_case_ids` queries `system = ANY(%s)` with `stored_system_names`; a
  `long_context_multi` run records `fit_summary` in `report.config["fit_by_k"]` and prints it.
- `test/unit/test_long_context_wiring.py::test_run_suite_records_the_long_context_model` then reads
  `long_context_oracle`.
- `docs/design/XbrlRag_Evaluation.md` (the `WS9-E5` row), `XbrlRag_Eval_v2_Architecture.md` and the
  evaluation guide name the arms. `WS10-H2` stays `todo` until `html_rag`; its `long_context_resolved`
  check starts passing here.

## Notes

**What was checked before writing this**, read-only on `xbrl_db`, 2026-10-30:

- `filing_registry` carries the corrected label. HD reads 2023, 2024, 2025 for period ends
  2024-01-28, 2025-02-02, 2026-02-01, with `entry_url` under `HD/2024`, `HD/2025`, `HD/2026`. CRM
  reads 2023-2026. So the explicit-year path picks the right filing, and the label and the path's
  year really do differ, which is why requirement 6 forbids deriving a key.
- **`entity_master.last_seen_fy` is not corrected.** `scripts/ddl/populate/entity_master.sql` takes
  the highest raw DEI of any form. Live: CRM 2025 (its label is 2026) and NVDA 2027 (from a 10-Q;
  no annual filing has that label). HD 2025 and AAPL 2026 are right. So "most recent" and "last
  year" pick CRM's January-2025 filing, and refuse for NVDA. These arms inherit this on purpose,
  because XbrlRag's own lookup gets the same year: that is the point of sharing the resolver. The
  fix belongs in a change of its own, which moves both systems together.
- `eval.eval_run` has 6 rows with `system = 'long_context'`, all from 2026-10-28, and no other
  `long*` name.

**Names that must not break.**

| Name | Where | Treatment |
|---|---|---|
| run ids `eval-<hex>` | `eval.eval_run`, `docs/eval/external_test_v1/README.md` | a uuid, independent of the system; unaffected |
| `system = 'long_context'` | persisted rows | left alone; found through `stored_system_names` |
| `v_run_trend`, `/api/eval?system=` | grouped or filtered on `system` | the oracle arm's series restarts under its new name; the old rows stay visible under `long_context`. Accepted, and better than rewriting history that the external-test report cites |
| `eval_stats.py` | reads runs by id; the label is the config's | unaffected |
| `--system long_context` | `docs/eval/external_test_v1/sealed/` | sealed, and run at the freeze tag; the alias keeps it working on `main` too. Sealed files are not edited |
| stage `long_context` | gateway, prompt registry, cost events | a stage, not a system: unchanged |

**Why a refusal and not XbrlRag's search.** XbrlRag's degraded branch can still answer from chunks
when the filer is unresolved or a year is ambiguous. Doing the same here would add Qdrant and
RagInference to an arm whose idea is the absence of retrieval. So on those cases the arm is a
little worse off than XbrlRag, in one direction and visibly (status `undecided`).

**The decomposer is not run.** It is a separate stage, and a compound question reaches the router
whole. Compound cases are rare outside screens. The effect for a pair of different years ("A in
2023 against B in 2024") is that the product asks for four filings and not two: the right ones are
included, at more cost and a worse fit. A question the router sends to a screen names no company,
which is the undecided path.

**A too-many or over-window exclusion is the one place the resolver can affect the denominator of
`long_context_multi`**: a router that over-extracts mentions can push a case out. That is what
`selection` and `fit_summary` are for, and it should be read alongside the exclusion count.

**Window and cost.** The 66 filings measured for [long-context-arm](long-context-arm.md) reduce to
about 18K-203K tokens, median about 102K. At 250K tokens two median filings fit (204K) and three do
not (306K). So most pairs and two-year comparisons fit, and a three-year trend fits only for small
filers. A wider window means adding a long-context price tier to `pricing.yaml` first, then
lifting requirement 15. gpt-5.6-sol allows 922K input tokens. Three median filings would then cost
about $2.45 uncached at 2x input, against about $0.41 for one. Only cases on the same set of
filings are sure to share a cached prefix, so expect fewer cache hits than on the oracle arm.

**Quarters.** A question about a quarter resolves its fiscal year and reads the annual filing, as the
oracle arm does.

**Retired tests changed in this spec PR.** `test/spec/baseline-arms/test_ba_c7_selection.py` and
`test/spec/long-context-arm/test_lc_c6_model_selection.py` pin the old five-name tuple and the old
name. CI requires graduated tests to pass before and after this change, so they are loosened to
hold on both sides: the first four names, and either long-context name. C1 here pins the exact
tuple.

**Not in spec tests:** the prompt's wording; `run_suite`'s `--model`, `fit_by_k` and `--system
long_context` wiring (unit tests, by the implementer); the default router's construction against
real services; the live pilot, which the implementer runs and reports. Run all of `test/spec` after
implementing.

**Oracles.** Hand-built cases, router replies, filings and registry rows. Home Depot's rows are
copied from the live registry as read above, and its labels are in
[fiscal-label-alignment](fiscal-label-alignment.md). AAA, BBB and CCC are synthetic. No held-out
filer appears anywhere.
