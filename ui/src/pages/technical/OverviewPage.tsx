import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { BranchMixRow, DailyRow } from "../../lib/types";
import { Card, Empty, Tile, ms } from "../../components/ui";
import { BarRank, MixChart, TrendChart, fmtInt, fmtUsd } from "../../components/charts";

const shortDay = (d?: string) => (d && d.length >= 10 ? d.slice(5, 10) : d ?? "");

export default function OverviewPage() {
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const metrics = useQuery({ queryKey: ["metrics-overview"], queryFn: api.metricsOverview });

  const latency = (metrics.data?.latency ?? {}) as Record<string, number>;
  const dailyDesc = metrics.data?.daily ?? [];
  const stages = metrics.data?.stages ?? [];
  const branchMix = metrics.data?.branch_mix ?? [];
  const dbOk = health.data?.audit_db;

  // Charts read oldest→newest; the endpoint returns newest-first.
  const daily = useMemo(
    () => [...dailyDesc].reverse().map((r) => ({ ...r, label: shortDay(r.day) })),
    [dailyDesc],
  );

  // 30-day rollups for the KPI band.
  const totals = useMemo(() => {
    const sum = (k: keyof DailyRow) => dailyDesc.reduce((a, r) => a + (Number(r[k]) || 0), 0);
    return { queries: sum("queries"), errored: sum("errored"), cost: sum("cost_usd") };
  }, [dailyDesc]);

  // Donut of query volume by branch (aggregate the branch×intent rows).
  const branchSlices = useMemo(() => {
    const by = new Map<string, number>();
    branchMix.forEach((r: BranchMixRow) => {
      const k = r.branch || "unknown";
      by.set(k, (by.get(k) ?? 0) + (Number(r.queries) || 0));
    });
    return [...by.entries()]
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }, [branchMix]);

  const stageBars = useMemo(
    () =>
      stages
        .filter((s) => s.p95_ms != null)
        .slice(0, 8)
        .map((s) => ({ label: String(s.stage ?? "—"), value: Number(s.p95_ms) })),
    [stages],
  );

  const hasDaily = daily.length > 0;

  return (
    <div>
      <h1 className="page-title">System Overview</h1>
      <p className="page-sub">Health, SLA latencies, cost and volume trends — from the audit analyst views.</p>

      <div className="grid cols-4">
        <Tile label="Queries (30d)" value={fmtInt(totals.queries)} />
        <Tile label="Errored (30d)" value={fmtInt(totals.errored)} />
        <Tile label="Cost (30d)" value={totals.cost ? `$${totals.cost.toFixed(2)}` : "—"} />
        <Tile label="p95 latency" value={ms(latency.p95_ms)} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card title="Cost per day" hint="audit.v_query_daily — last 30 days">
          {hasDaily ? (
            <TrendChart
              data={daily}
              xKey="label"
              area
              height={190}
              yFmt={(v) => fmtUsd(Number(v))}
              series={[{ key: "cost_usd", label: "Cost (USD)", colorIndex: 0 }]}
            />
          ) : <Empty>No daily data yet.</Empty>}
        </Card>

        <Card title="Query volume per day" hint="total vs. errored">
          {hasDaily ? (
            <TrendChart
              data={daily}
              xKey="label"
              height={190}
              yFmt={(v) => fmtInt(Number(v))}
              series={[
                { key: "queries", label: "Queries", colorIndex: 0 },
                { key: "errored", label: "Errored", colorIndex: 5 },
              ]}
            />
          ) : <Empty>No daily data yet.</Empty>}
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="p95 latency per day" hint="audit.v_query_daily">
          {hasDaily ? (
            <TrendChart
              data={daily}
              xKey="label"
              height={190}
              yFmt={(v) => ms(Number(v))}
              series={[{ key: "p95_latency_ms", label: "p95 latency", colorIndex: 1 }]}
            />
          ) : <Empty>No daily data yet.</Empty>}
        </Card>

        <Card title="Query mix by branch" hint="audit.v_branch_mix — click a slice to filter traces">
          {branchSlices.length ? (
            <MixChart
              slices={branchSlices.map((s) => ({
                ...s,
                onClick: () => navigate(`/tech/traces?branch=${encodeURIComponent(s.label)}`),
              }))}
              height={180}
              valueFmt={(v) => `${fmtInt(Number(v))} queries`}
            />
          ) : <Empty>No branch data yet.</Empty>}
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Latency percentiles" hint="audit.v_query_latency">
          {latency && Object.keys(latency).length ? (
            <table>
              <tbody>
                {["avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"].map((k) => (
                  <tr key={k}><td className="dim">{k}</td><td className="mono">{ms(latency[k])}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No latency data.</Empty>}
        </Card>

        <Card title="Stage latency (p95)" hint="audit.v_stage_latency — slowest stages">
          {stageBars.length ? (
            <BarRank rows={stageBars} valueFmt={(v) => ms(Number(v))} colorIndex={2} />
          ) : <Empty>No stage data.</Empty>}
        </Card>
      </div>

      <Card title="BFF health">
        {health.isLoading ? <p className="dim">Checking…</p> : (
          <table>
            <tbody>
              <tr><td className="dim">Audit DB</td><td>{dbOk ? "reachable" : `unreachable — ${health.data?.audit_db_error ?? ""}`}</td></tr>
              {Object.entries(health.data?.pipelines ?? {}).map(([k, v]) => (
                <tr key={k}><td className="dim">pipeline: {k}</td><td>{v ? "importable" : "import failed"}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {!dbOk && !health.isLoading ? (
          <p className="dim mt-8 mb-0">
            Observability panels populate once the BFF can read the <code className="mono">audit</code> schema
            (set <code className="mono">XBRL_DB_*</code> env vars).
          </p>
        ) : null}
      </Card>
    </div>
  );
}
