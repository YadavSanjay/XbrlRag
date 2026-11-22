// Minimal typed fetch client against the BFF (/api, proxied by Vite to FastAPI).
import type {
  BranchMixRow,
  BridgeResponse,
  CoverageItem,
  DailyRow,
  FactLookupRequest,
  FactLookupResponse,
  EvalCaseRow,
  EvalComparison,
  EvalRun,
  EvalFlakyRow,
  EvalRunDetail,
  EvalSilentWrongRow,
  EvalSuiteRow,
  Health,
  ScreenRequest,
  ScreenResponse,
  LlmCallRow,
  MetricsGridResponse,
  ModelCostRow,
  QueryMode,
  QueryResponse,
  StageLatencyRow,
  TraceDetail,
  TraceHeader,
} from "./types";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => http<Health>("/api/health"),

  runQuery: (body: { query: string; mode: QueryMode; user_id?: string; top_k?: number }) =>
    http<QueryResponse>("/api/query", { method: "POST", body: JSON.stringify(body) }),

  // URL for the SSE stream — consumed via EventSource (GET-only) in the Ask page.
  streamUrl: (params: { query: string; mode: QueryMode; user_id?: string }) => {
    const p = new URLSearchParams({ query: params.query, mode: params.mode });
    if (params.user_id) p.set("user_id", params.user_id);
    return `/api/query/stream?${p.toString()}`;
  },

  companies: (q: string) =>
    http<{ items: Array<{ ticker: string; company_name: string; cik: string }> }>(
      `/api/companies?q=${encodeURIComponent(q)}`,
    ),

  // ---- v1 Analyst API: discovery + deterministic facts ----
  coverage: (ticker?: string) =>
    http<{ items: CoverageItem[] }>(
      `/api/v1/coverage${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`,
    ),

  factsLookup: (body: FactLookupRequest) =>
    http<FactLookupResponse>("/api/v1/facts/lookup", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  factsBridge: (body: {
    company: string;
    concept: string;
    fiscal_year: number;
    dimension: string;
    fiscal_period?: string;
  }) =>
    http<BridgeResponse>("/api/v1/facts/bridge", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  screen: (body: ScreenRequest) =>
    http<ScreenResponse>("/api/v1/screen", { method: "POST", body: JSON.stringify(body) }),

  // ---- v1 Analyst API: metrics grid (tabular / CSV) ----
  metricsCatalog: () =>
    http<{ items: Array<{ metric_name: string; formula: string | null }> }>(
      "/api/v1/metrics/catalog",
    ),

  metricsGrid: (body: {
    companies: string[];
    metrics: string[];
    fiscal_years: number[];
    fiscal_period: string;
  }) =>
    http<MetricsGridResponse>("/api/v1/metrics", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // POSTs the same request to /metrics.csv and triggers a file download.
  downloadMetricsCsv: async (body: {
    companies: string[];
    metrics: string[];
    fiscal_years: number[];
    fiscal_period: string;
  }) => {
    const res = await fetch("/api/v1/metrics.csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `metrics_${body.fiscal_period || "FY"}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },

  traces: (limit = 50) =>
    http<{ items: TraceHeader[] }>(`/api/traces?limit=${limit}`),

  trace: (queryId: string) => http<TraceDetail>(`/api/traces/${encodeURIComponent(queryId)}`),

  lineage: (queryId: string) =>
    http<{ items: Array<Record<string, unknown>> }>(`/api/lineage/${encodeURIComponent(queryId)}`),

  // Reverse audit: which answers used a given fact.
  factQueries: (factId: number | string) =>
    http<{ items: Array<Record<string, unknown>> }>(`/api/facts/${encodeURIComponent(String(factId))}/queries`),

  metricsOverview: () =>
    http<{
      daily: DailyRow[];
      latency: Record<string, number> | null;
      branch_mix: BranchMixRow[];
      stages: StageLatencyRow[];
    }>("/api/metrics/overview"),

  metricsLlm: () =>
    http<{ calls: LlmCallRow[]; model_cost: ModelCostRow[] }>("/api/metrics/llm"),

  ingestionRuns: () =>
    http<{ items: Array<Record<string, unknown>> }>("/api/ingestion/runs"),

  ingestionRunItems: (runId: string) =>
    http<{ items: Array<Record<string, unknown>> }>(
      `/api/ingestion/runs/${encodeURIComponent(runId)}/items`,
    ),

  ingestionAnalytics: () =>
    http<{
      failures: Array<Record<string, unknown>>;
      retries: Array<Record<string, unknown>>;
      daily: Array<Record<string, unknown>>;
    }>("/api/ingestion/analytics"),

  prompts: () =>
    http<{ items: Array<{ name: string; version: string; chars: number }>; root: string }>(
      "/api/prompts",
    ),

  logs: (opts: { limit?: number; query_id?: string; level?: string } = {}) => {
    const p = new URLSearchParams();
    if (opts.limit) p.set("limit", String(opts.limit));
    if (opts.query_id) p.set("query_id", opts.query_id);
    if (opts.level) p.set("level", opts.level);
    return http<{ items: Array<Record<string, unknown>>; path: string; error?: string }>(
      `/api/logs?${p.toString()}`,
    );
  },

  // --- WS-9 evaluation ------------------------------------------------------
  evalRuns: (opts: { suite?: string; system?: string; limit?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.suite) p.set("suite", opts.suite);
    if (opts.system) p.set("system", opts.system);
    p.set("limit", String(opts.limit ?? 50));
    return http<{ items: EvalRun[] }>(`/api/eval/runs?${p.toString()}`);
  },

  evalRun: (runId: string) =>
    http<EvalRunDetail>(`/api/eval/runs/${encodeURIComponent(runId)}`),

  evalCases: (
    runId: string,
    opts: {
      grade?: string;
      kind?: string;
      tag?: string;
      ticker?: string;
      failed_only?: boolean;
      silent_wrong?: boolean;
      confident_wrong?: boolean;
      limit?: number;
    } = {},
  ) => {
    const p = new URLSearchParams();
    if (opts.grade) p.set("grade", opts.grade);
    if (opts.kind) p.set("kind", opts.kind);
    if (opts.tag) p.set("tag", opts.tag);
    if (opts.ticker) p.set("ticker", opts.ticker);
    if (opts.failed_only) p.set("failed_only", "true");
    if (opts.silent_wrong) p.set("silent_wrong", "true");
    if (opts.confident_wrong) p.set("confident_wrong", "true");
    p.set("limit", String(opts.limit ?? 500));
    return http<{ items: EvalCaseRow[]; total: number }>(
      `/api/eval/runs/${encodeURIComponent(runId)}/cases?${p.toString()}`,
    );
  },

  evalCompare: (a: string, b: string) =>
    http<EvalComparison>(
      `/api/eval/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    ),

  evalSuites: () => http<{ items: EvalSuiteRow[] }>("/api/eval/suites"),

  // Release-gate queue: confident + cited + wrong, across every run.
  evalSilentWrong: (limit = 100) =>
    http<{ items: EvalSilentWrongRow[] }>(`/api/eval/silent-wrong?limit=${limit}`),

  // Cases whose grade flips between runs (usually LLM non-determinism).
  evalFlaky: (opts: { suite?: string; limit?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.suite) p.set("suite", opts.suite);
    p.set("limit", String(opts.limit ?? 100));
    return http<{ items: EvalFlakyRow[] }>(`/api/eval/flaky?${p.toString()}`);
  },
};
