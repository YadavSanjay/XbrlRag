import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { BranchMixRow, DailyRow, LlmCallRow, ModelCostRow } from "../../lib/types";
import { Badge, Card, Empty, KV, SlideOver, Tile, ms } from "../../components/ui";
import { BarRank, TrendChart, fmtInt, fmtUsd, fmtUsdFull } from "../../components/charts";

const shortDay = (d?: string) => (d && d.length >= 10 ? d.slice(5, 10) : d ?? "");
const n = (v: unknown) => Number(v) || 0;

export default function LlmPage() {
  const llm = useQuery({ queryKey: ["metrics-llm"], queryFn: api.metricsLlm });
  const overview = useQuery({ queryKey: ["metrics-overview"], queryFn: api.metricsOverview });
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: api.prompts });

  const [drillModel, setDrillModel] = useState<string | null>(null);

  const calls = llm.data?.calls ?? [];
  const modelCost = llm.data?.model_cost ?? [];
  const daily = overview.data?.daily ?? [];
  const branchMix = overview.data?.branch_mix ?? [];

  const dailyAsc = useMemo(
    () => [...daily].reverse().map((r: DailyRow) => ({ ...r, label: shortDay(r.day) })),
    [daily],
  );

  const kpis = useMemo(() => {
    const spend = daily.reduce((a, r) => a + n(r.cost_usd), 0);
    const queries = daily.reduce((a, r) => a + n(r.queries), 0);
    const tokens = modelCost.reduce((a, r) => a + n(r.prompt_tokens) + n(r.completion_tokens), 0);
    const llmCalls = modelCost.reduce((a, r) => a + n(r.calls), 0);
    return {
      spend,
      queries,
      tokens,
      llmCalls,
      costPerQuery: queries ? spend / queries : 0,
    };
  }, [daily, modelCost]);

  const modelBars = useMemo(
    () =>
      [...modelCost]
        .map((r: ModelCostRow) => ({ label: String(r.model ?? "—"), value: n(r.cost_usd) }))
        .sort((a, b) => b.value - a.value),
    [modelCost],
  );

  const stageBars = useMemo(() => {
    const by = new Map<string, number>();
    calls.forEach((r: LlmCallRow) => by.set(r.stage || "—", (by.get(r.stage || "—") ?? 0) + n(r.cost_usd)));
    return [...by.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  }, [calls]);

  const branchBars = useMemo(() => {
    const by = new Map<string, number>();
    branchMix.forEach((r: BranchMixRow) => {
      const k = r.branch || "unknown";
      by.set(k, (by.get(k) ?? 0) + n(r.avg_cost_usd) * n(r.queries));
    });
    return [...by.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  }, [branchMix]);

  const drillRows = useMemo(
    () => calls.filter((r: LlmCallRow) => r.model === drillModel),
    [calls, drillModel],
  );

  return (
    <div>
      <h1 className="page-title">Cost & LLM Economics</h1>
      <p className="page-sub">Where the spend goes — by model, stage, branch and prompt version. From the audit analyst views.</p>

      <div className="grid cols-4">
        <Tile label="LLM spend (30d)" value={kpis.spend ? `$${kpis.spend.toFixed(2)}` : "—"} />
        <Tile label="Cost / query (30d)" value={kpis.costPerQuery ? fmtUsdFull(kpis.costPerQuery) : "—"} />
        <Tile label="Tokens (all-time)" value={fmtInt(kpis.tokens)} />
        <Tile label="LLM calls (all-time)" value={fmtInt(kpis.llmCalls)} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card title="Spend per day" hint="audit.v_query_daily — last 30 days">
          {dailyAsc.length ? (
            <TrendChart
              data={dailyAsc}
              xKey="label"
              area
              height={190}
              yFmt={(v) => fmtUsd(Number(v))}
              series={[{ key: "cost_usd", label: "Cost (USD)", colorIndex: 0 }]}
            />
          ) : <Empty>No daily data yet.</Empty>}
        </Card>

        <Card title="Cost by model" hint="audit.v_model_cost — click a bar to drill in">
          {modelBars.length ? (
            <BarRank
              rows={modelBars}
              valueFmt={(v) => fmtUsd(Number(v))}
              colorIndex={0}
              onRowClick={(r) => setDrillModel(r.label)}
            />
          ) : <Empty>No model cost data.</Empty>}
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Cost by stage" hint="audit.v_llm_calls grouped by stage">
          {stageBars.length ? (
            <BarRank rows={stageBars} valueFmt={(v) => fmtUsd(Number(v))} colorIndex={4} />
          ) : <Empty>No stage cost data.</Empty>}
        </Card>

        <Card title="Cost by branch" hint="audit.v_branch_mix (avg cost × queries)">
          {branchBars.length ? (
            <BarRank rows={branchBars} valueFmt={(v) => fmtUsd(Number(v))} colorIndex={2} />
          ) : <Empty>No branch cost data.</Empty>}
        </Card>
      </div>

      <Card title="LLM calls by stage · model · prompt version" hint="audit.v_llm_calls — group repeats across prompt_version to spot drift">
        {calls.length ? (
          <table>
            <thead>
              <tr>
                <th>Stage</th><th>Model</th><th>Prompt ver.</th><th>Calls</th><th>Errors</th><th>Refusals</th>
                <th>p95</th><th>Prompt tok</th><th>Compl. tok</th><th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((r: LlmCallRow, i) => (
                <tr key={i} className="click" onClick={() => r.model && setDrillModel(r.model)}>
                  <td>{r.stage}</td>
                  <td className="mono">{r.model}</td>
                  <td>{r.prompt_version ? <Badge tone="purple">{r.prompt_version}</Badge> : "—"}</td>
                  <td className="mono">{fmtInt(r.calls)}</td>
                  <td className="mono">{r.errors ? <span style={{ color: "var(--red)" }}>{r.errors}</span> : "0"}</td>
                  <td className="mono">{r.refusals ? <span style={{ color: "var(--red)" }}>{r.refusals}</span> : "0"}</td>
                  <td className="mono">{ms(r.p95_latency_ms)}</td>
                  <td className="mono">{fmtInt(r.prompt_tokens)}</td>
                  <td className="mono">{fmtInt(r.completion_tokens)}</td>
                  <td className="mono">{fmtUsdFull(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No LLM call data.</Empty>}
      </Card>

      <Card title="Prompt registry" hint={prompts.data?.root}>
        {(prompts.data?.items ?? []).length ? (
          <table>
            <thead><tr><th>Template</th><th>Version</th><th>Chars</th></tr></thead>
            <tbody>
              {prompts.data!.items.map((p) => (
                <tr key={p.name}>
                  <td className="mono">{p.name}</td>
                  <td><Badge tone="purple">{p.version}</Badge></td>
                  <td className="dim">{p.chars}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No prompt templates found.</Empty>}
      </Card>

      {drillModel && (
        <SlideOver title={<span>Model · <span className="mono">{drillModel}</span></span>} onClose={() => setDrillModel(null)}>
          {(() => {
            const totalCost = drillRows.reduce((a, r) => a + n(r.cost_usd), 0);
            const totalCalls = drillRows.reduce((a, r) => a + n(r.calls), 0);
            const pt = drillRows.reduce((a, r) => a + n(r.prompt_tokens), 0);
            const ct = drillRows.reduce((a, r) => a + n(r.completion_tokens), 0);
            return (
              <>
                <KV rows={[
                  ["Total cost", <span className="mono">{fmtUsdFull(totalCost)}</span>],
                  ["Total calls", <span className="mono">{fmtInt(totalCalls)}</span>],
                  ["Prompt tokens", <span className="mono">{fmtInt(pt)}</span>],
                  ["Completion tokens", <span className="mono">{fmtInt(ct)}</span>],
                ]} />
                <h4 style={{ margin: "16px 0 6px", fontSize: 13 }}>By stage · prompt version</h4>
                <table>
                  <thead><tr><th>Stage</th><th>Ver.</th><th>Calls</th><th>Cost</th></tr></thead>
                  <tbody>
                    {drillRows.map((r: LlmCallRow, i) => (
                      <tr key={i}>
                        <td>{r.stage}</td>
                        <td className="mono">{r.prompt_version ?? "—"}</td>
                        <td className="mono">{fmtInt(r.calls)}</td>
                        <td className="mono">{fmtUsdFull(r.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="dim mt-8" style={{ fontSize: 12 }}>
                  Per-call reasoning-token detail is on each query's trace (LLM calls panel).
                </p>
              </>
            );
          })()}
        </SlideOver>
      )}
    </div>
  );
}
