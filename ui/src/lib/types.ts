// Types mirror the BFF responses (src/api/schemas.py) and the pipeline answer
// shapes (RagResponse / XbrlRagResponse). Kept loose where the backend returns
// pass-through dicts.

export type QueryMode = "auto" | "xbrl" | "basic" | "hyde" | "subquery";

// v1 Analyst API — metrics grid (tabular / CSV)
export interface MetricCell {
  cik?: string | null;
  ticker?: string | null;
  fiscal_year: number;
  fiscal_period: string;
  metric: string;
  kind: "derived" | "fact";
  value?: number | null;
  unit?: string | null;
  formula?: string | null;
  component_fact_ids?: Record<string, unknown> | null;
  citation?: Record<string, unknown> | null;
}

export interface ResolvedCompany {
  cik?: string | null;
  ticker?: string | null;
  company_name?: string | null;
  fye_month?: number | null;
  fye_day?: number | null;
  last_seen_fy?: number | null;
  resolution: string;
  similarity?: number;
}

// ---- v1: coverage / discovery ----
export interface CoverageItem {
  cik: string;
  ticker?: string | null;
  company_name?: string | null;
  fiscal_years: number[];
  form_types: string[];
}

// ---- v1: facts lookup (deterministic time series + point-in-time) ----
export interface FactPoint {
  fiscal_year: number;
  status: string; // OK | AMBIGUOUS | NOT_FOUND | UNRESOLVED_DIMENSION | ERROR
  value?: number | null;
  unit?: string | null;
  confidence?: number;
  citation?: Record<string, unknown> | null;
  notes?: string[];
  error?: string | null;
}

export interface FactLookupRequest {
  company: string;
  concept: string;
  fiscal_years: number[];
  dimension?: Record<string, string> | null;
  fiscal_period?: string;
  ytd?: boolean;
  as_of?: string | null;
  as_originally_reported?: boolean;
}

// Paid-model spend behind one v1 response; cost_usd is 0.0 when no paid call was made.
export interface Usage {
  cost_usd: number;
  llm_calls: number;
}

export interface FactLookupResponse {
  company: ResolvedCompany;
  concept: string;
  unresolved: boolean;
  series: FactPoint[];
  usage: Usage;
}

// ---- v1: cross-filer screen (ranking) ----
export interface ScreenRequest {
  concept_family: string;
  fy: number;
  dimension?: Record<string, string> | null;
  metric_kind?: string; // value | pct_of_total | derived:<Name>
  order?: string; // desc | asc
  top_n?: number | null;
  min_threshold?: number | null;
  fiscal_period?: string;
  ytd?: boolean;
}

export interface ScreenRow {
  rank?: number | null;
  ticker?: string | null;
  cik?: string | null;
  fiscal_year?: number | null;
  value?: number | null;
  unit?: string | null;
  pct_of_total?: number | null;
  label?: string | null;
  fact_id?: string | null;
  formula?: string | null;
  component_fact_ids?: Record<string, unknown> | null;
  citation?: Record<string, unknown> | null;
}

export interface ScreenResponse {
  status: string;
  concept_family: string;
  fy: number;
  metric_kind: string;
  n_rows: number;
  rows: ScreenRow[];
  resolution_notes: string[];
  error?: string | null;
}

// ---- v1: variance bridge ----
export interface BridgeComponent {
  member?: string | null;
  prior?: number | null;
  current?: number | null;
  change?: number | null;
  pct_change?: number | null;
  fact_id_current?: number | null;
  fact_id_prior?: number | null;
}

export interface BridgeResponse {
  company: ResolvedCompany;
  concept: string;
  fiscal_year: number;
  fiscal_period: string;
  dimension: string;
  total_prior?: number | null;
  total_current?: number | null;
  total_change?: number | null;
  explained_change?: number | null;
  unexplained?: number | null;
  components: BridgeComponent[];
}

export interface MetricsGridResponse {
  companies: ResolvedCompany[];
  fiscal_period: string;
  cells: MetricCell[];
}

export interface AnswerDetail {
  statement: string;
  fact_ids?: string[];
  chunk_ids?: string[];
}

export interface UnansweredPart {
  question_fragment: string;
  reason: string;
}

// The raw RagResponse / XbrlRagResponse dict (fields are optional across paths).
export interface AnswerPayload {
  query?: string;
  answer_summary?: string;
  answer_details?: AnswerDetail[];
  unanswered_parts?: UnansweredPart[];
  metadata?: Record<string, unknown>;
  plan?: { steps?: PlanStep[] } & Record<string, unknown>;
  execution_result?: ExecutionResult;
  narrative_chunks?: Array<Record<string, unknown>>;
  retrieved_chunks?: Array<[string, string]>;
}

export interface PlanStep {
  id: string;
  op: "lookup" | "compute" | "screen" | "breakdown" | "narrative" | string;
  concept?: string;
  concept_family?: string | null;
  metric_kind?: string | null;
  expr?: string | null;
  ticker?: string | null;
  cik?: string | null;
  fy?: number | null;
  axis?: string | null;
  // The planner emits a dict of axis → member hint, or the string "NONE".
  dimensions?: Record<string, string> | string | null;
  order?: string | null;
  top_n?: number | null;
  [k: string]: unknown;
}

// One row of a `screen` (ranked, cross-filer) or `breakdown` (one filer's
// partition) step. A derived-metric row has a synthetic `fact_id`
// ("FreeCashFlow:<cik>:<fy>") and names its audited inputs in `component_fact_ids`.
export interface StepRow {
  rank?: number | null;
  ticker?: string | null;
  cik?: string | null;
  fiscal_year?: number | null;
  label?: string | null;
  member?: string | null;
  value?: number | null;
  unit?: string | null;
  pct_of_total?: number | null;
  fact_id?: string | null;
  formula?: string | null;
  component_fact_ids?: Record<string, unknown> | null;
  citation?: Record<string, unknown> | null;
  [k: string]: unknown;
}

export interface StepResult {
  step_id?: string;
  op?: string;
  status?: string;
  value?: number | null;
  unit?: string | null;
  expr?: string;
  citation?: Record<string, unknown> | null;
  confidence?: number | null;
  linked_chunk_ids?: string[];
  error?: string | null;
  // screen / breakdown: the ranked or partition rows.
  rows?: StepRow[];
  // screen: filers left out because they have no figure for the requested year.
  excluded?: Array<{ ticker?: string | null; latest_fiscal_year?: number | null; [k: string]: unknown }>;
  // An executor fallback result: the sibling steps an intersection lookup merged,
  // or the components a derived lookup (e.g. gross profit) was computed from.
  derived_from?: string[];
  dimensions?: Record<string, string> | null;
  component_fact_ids?: Record<string, unknown> | null;
}

export interface ExecutionResult {
  status?: string;
  steps?: StepResult[];
  bindings?: Record<string, number | null>;
  error?: string | null;
}

export interface FaithfulnessVerdictRow {
  index?: number;
  statement?: string;
  verdict?: string; // SUPPORTED | PARTIALLY_SUPPORTED | UNSUPPORTED
  reason?: string;
  fact_ids?: string[];
  chunk_ids?: string[];
}

export interface FaithfulnessReport {
  status?: string;
  all_supported?: boolean;
  verdicts?: FaithfulnessVerdictRow[];
  [k: string]: unknown;
}

// One entry of `metadata.subqueries` on a decomposed (compound) answer.
export interface SubQueryMeta {
  id?: string;
  query?: string;
  depends_on?: string[];
  fully_answered?: boolean | string | null;
  plan?: { steps?: PlanStep[] } | null;
  execution_result?: ExecutionResult | null;
  faithfulness?: FaithfulnessReport | null;
}

export interface QueryResponse {
  query_id: string | null;
  mode: QueryMode;
  answer: AnswerPayload;
  error?: string | null;
}

export interface TraceHeader {
  query_id: string;
  query: string;
  branch?: string;
  intent?: string;
  degraded?: boolean;
  fully_answered?: boolean;
  total_latency_ms?: number;
  total_cost_usd?: number;
  llm_calls?: number;
  total_retries?: number;
  error?: string | null;
  started_at?: string;
  stage_latency_ms?: Record<string, number>;
  // Full final answer (XbrlRagResponse) captured with the trace, so the Trace
  // Explorer can render the same view the Ask page shows.
  answer?: AnswerPayload | null;
}

// Payload of an `llm_call` audit event — token/cost/latency plus (when
// XBRLRAG_TRACE_LLM_BODIES is on) the exact system/user prompt and response text.
export interface LlmCallEvent {
  stage?: string;
  model?: string;
  outcome?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  reasoning_tokens?: number;
  reasoning_effort?: string | null;
  cost_usd?: number;
  latency_ms?: number;
  retries?: number;
  finish_reason?: string;
  system?: string;
  user?: string;
  response?: string;
  prompt_version?: string;
}

// ---- observability analyst-view rows (loose; extra columns ignored) ----
export interface DailyRow {
  day: string;
  queries?: number;
  errored?: number;
  degraded?: number;
  fully_answered?: number;
  cost_usd?: number;
  avg_cost_usd?: number;
  total_tokens?: number;
  llm_calls?: number;
  total_retries?: number;
  avg_latency_ms?: number;
  p95_latency_ms?: number;
}

export interface BranchMixRow {
  branch?: string;
  intent?: string;
  queries?: number;
  pct?: number;
  degraded?: number;
  avg_cost_usd?: number;
  avg_latency_ms?: number;
}

export interface ModelCostRow {
  model?: string;
  calls?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_usd?: number;
}

export interface LlmCallRow {
  stage?: string;
  model?: string;
  prompt_version?: string;
  calls?: number;
  errors?: number;
  refusals?: number;
  retries?: number;
  avg_latency_ms?: number;
  p95_latency_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_usd?: number;
}

export interface StageLatencyRow {
  stage?: string;
  p50_ms?: number;
  p95_ms?: number;
  p99_ms?: number;
  avg_ms?: number;
  samples?: number;
}

// Payload of a `sql_query` audit event — the exact XBRL DB query that ran.
export interface SqlQueryEvent {
  source?: string;
  sql?: string;
  n_rows?: number;
  latency_ms?: number;
}

export interface TraceEvent {
  seq: number;
  kind: string;
  stage: string;
  payload: Record<string, unknown>;
}

export interface TraceDetail {
  header: TraceHeader | null;
  events: TraceEvent[];
}

// A single SSE event from /api/query/stream (a streamed trace event, or the
// terminal "result" / control events).
export interface StreamEvent {
  kind: string;
  stage?: string;
  query_id?: string;
  answer?: AnswerPayload;
  error?: string;
  mode?: QueryMode;
  [k: string]: unknown;
}

export interface Health {
  status: string;
  audit_db: boolean;
  audit_db_error?: string | null;
  pipelines: Record<string, boolean>;
}

// ---------------------------------------------------------------------------
// WS-9 evaluation (docs/design/XbrlRag_Evaluation.md)
// ---------------------------------------------------------------------------

export interface EvalRun {
  run_id: string;
  suite: string;
  suite_version?: string | null;
  system: string;
  tier?: string | null;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  n_cases: number;
  n_skipped: number;
  n_correct: number;
  n_partial: number;
  n_incorrect: number;
  n_refused: number;
  n_error: number;
  // Grader-honesty fields: absent/NULL on runs graded before grader versioning.
  n_hedged_miss?: number | null;
  n_needs_review?: number | null;
  // Arm-neutral fields (grader version 3): NULL on runs graded before it.
  n_confident_wrong?: number | null;
  // Precision-aware fields (grader version 4): NULL on runs graded before it.
  n_imprecise?: number | null;
  accuracy?: number | null;
  value_accuracy?: number | null;
  citation_rate?: number | null;
  grounding_precision?: number | null;
  grounding_recall?: number | null;
  silent_wrong_rate?: number | null;
  confident_wrong_rate?: number | null;
  refusal_rate?: number | null;
  hedged_miss_rate?: number | null;
  imprecise_rate?: number | null;
  decline_rate?: number | null;
  error_rate?: number | null;
  grader_version?: string | null;
  paraphrase_stability?: number | null;
  total_cost_usd?: number | string | null;
  latency_p50_ms?: number | null;
  latency_p95_ms?: number | null;
  corpus_fingerprint?: string | null;
  git_sha?: string | null;
  // A narrowed run (--kind / --rerun-failed / --sample …) is tier='partial' and its
  // notes record the selection; its accuracy is over a biased subset, not a baseline.
  notes?: string | null;
  // Present on the list endpoint (joined from v_run_trend).
  accuracy_delta?: number | null;
  silent_wrong_delta?: number | null;
  prev_run_id?: string | null;
  corpus_changed?: boolean | null;
  by_kind?: Record<string, EvalKindMetrics> | null;
}

export interface EvalKindMetrics {
  n: number;
  n_correct: number;
  accuracy: number;
}

export interface EvalKindRow {
  kind: string;
  n: number;
  n_correct: number;
  accuracy: number;
}

export interface EvalTickerRow {
  ticker: string;
  fy: number | null;
  n: number;
  n_correct: number;
  accuracy: number | string;
}

export interface EvalInstabilityRow {
  paraphrase_group: string;
  n_variants: number;
  n_passed: number;
  case_ids: string[];
  example_question: string;
}

export interface EvalCaseRow {
  case_id: string;
  kind?: string | null;
  grade: string;
  passed: boolean;
  silent_wrong: boolean;
  needs_review?: boolean | null;
  confident_wrong?: boolean | null;
  value_correct?: boolean | null;
  question?: string | null;
  reason?: string | null;
  expected?: Record<string, unknown> | null;
  produced?: Record<string, unknown> | null;
  matched_value?: number | null;
  has_citation?: boolean | null;
  grounding_precision?: number | null;
  grounding_recall?: number | null;
  screen_metrics?: Record<string, unknown> | null;
  latency_ms?: number | null;
  cost_usd?: number | string | null;
  query_id?: string | null;
  paraphrase_group?: string | null;
  origin?: string | null;
  ticker?: string | null;
  fy?: number | null;
  tags?: string[] | null;
  error?: string | null;
}

export interface EvalRunDetail {
  run: EvalRun | null;
  by_kind: EvalKindRow[];
  by_ticker: EvalTickerRow[];
  instability: EvalInstabilityRow[];
}

export interface EvalFlippedCase {
  case_id: string;
  kind?: string | null;
  question?: string | null;
  grade_a: string;
  grade_b: string;
  passed_a: boolean;
  passed_b: boolean;
  reason_a?: string | null;
  reason_b?: string | null;
  query_id_a?: string | null;
  query_id_b?: string | null;
}

export interface EvalComparison {
  run_a: EvalRun | null;
  run_b: EvalRun | null;
  corpus_mismatch?: boolean;
  regressions: EvalFlippedCase[];
  improvements: EvalFlippedCase[];
}

// eval.v_silent_wrong — confident, cited and wrong, across all runs (the release gate).
export interface EvalSilentWrongRow {
  run_id: string;
  suite?: string | null;
  system?: string | null;
  created_at?: string | null;
  case_id: string;
  kind?: string | null;
  question?: string | null;
  reason?: string | null;
  expected_value?: unknown;
  produced_values?: unknown;
  expected_fact_ids?: unknown;
  produced_fact_ids?: unknown;
  query_id?: string | null;
}

// eval.v_case_flakiness — cases that passed in some runs and failed in others.
export interface EvalFlakyRow {
  suite: string;
  system: string;
  case_id: string;
  n_runs: number;
  n_passed: number;
  pass_rate: number | string;
  question?: string | null;
}

export interface EvalSuiteRow {
  suite: string;
  version: string;
  generated_at?: string | null;
  corpus_fingerprint?: string | null;
  n_cases: number;
  by_kind?: Record<string, number> | null;
  description?: string | null;
}
