import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { MetricCell, MetricsGridResponse } from "../../lib/types";
import { Card, Citation, Empty, KV, SlideOver } from "../../components/ui";

const PERIODS = ["FY", "Q1", "Q2", "Q3", "Q4"];
const DEFAULT_METRICS = ["Revenue", "NetIncome", "GrossMarginPct", "NetMarginPct", "ReturnOnEquityPct"];

/** Format a cell by metric convention: *Pct -> %, *Ratio/Turnover/Coverage -> x,
 *  Days* -> days, big currency facts -> compact, else localized number. */
function fmt(metric: string, v?: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (metric.endsWith("Pct")) return `${(v * 100).toFixed(1)}%`;
  if (metric.startsWith("Days")) return `${v.toFixed(0)}d`;
  if (/(Ratio|Turnover|Coverage|ToEquity)$/.test(metric)) return `${v.toFixed(2)}×`;
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function MetricsPage() {
  const [companiesRaw, setCompaniesRaw] = useState("NVDA, AAPL, MSFT");
  const [yearsRaw, setYearsRaw] = useState("2024, 2025, 2026");
  const [period, setPeriod] = useState("FY");
  const [metrics, setMetrics] = useState<string[]>(DEFAULT_METRICS);
  const [submitted, setSubmitted] = useState<null | {
    companies: string[]; metrics: string[]; fiscal_years: number[]; fiscal_period: string;
  }>(null);

  const catalog = useQuery({ queryKey: ["metricsCatalog"], queryFn: () => api.metricsCatalog() });

  const grid = useQuery({
    queryKey: ["metricsGrid", submitted],
    queryFn: () => api.metricsGrid(submitted!),
    enabled: !!submitted,
  });

  function parseList(s: string): string[] {
    return s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
  }

  function run() {
    const companies = parseList(companiesRaw);
    const fiscal_years = parseList(yearsRaw).map(Number).filter((n) => !Number.isNaN(n));
    if (!companies.length || !metrics.length || !fiscal_years.length) return;
    setSubmitted({ companies, metrics, fiscal_years, fiscal_period: period });
  }

  function toggleMetric(m: string) {
    setMetrics((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]));
  }

  return (
    <div>
      <h1 className="page-title">Metrics</h1>
      <p className="page-sub">
        A cited metric × period grid pulled straight from the audited fact store — no LLM. Pick
        companies, ratios/line-items and periods, then export to CSV for your model.
      </p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <label className="dim">Companies</label>
          <input className="text-input mono" style={{ width: 260 }} value={companiesRaw}
            onChange={(e) => setCompaniesRaw(e.target.value)} placeholder="NVDA, AAPL, MSFT" />
          <label className="dim">Years</label>
          <input className="text-input mono" style={{ width: 180 }} value={yearsRaw}
            onChange={(e) => setYearsRaw(e.target.value)} placeholder="2024, 2025, 2026" />
          <label className="dim">Period</label>
          <select className="text-input" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn" onClick={run}>Build grid</button>
          <button className="btn" disabled={!submitted}
            onClick={() => submitted && api.downloadMetricsCsv(submitted)}>Download CSV</button>
        </div>
      </Card>

      <Card title="Metrics" hint="derived ratios (from mv_derived_metrics) + raw line-items (concept families)">
        <MetricPicker
          catalog={(catalog.data?.items ?? []).map((i) => i.metric_name)}
          selected={metrics} onToggle={toggleMetric} />
      </Card>

      {grid.isFetching ? <Empty>Loading…</Empty> : null}
      {grid.isError ? <Empty>Couldn't load the grid. Check your selection, then build it again.</Empty> : null}
      {grid.data ? <GridTable data={grid.data} metrics={submitted!.metrics} /> : null}
      {!submitted ? <Empty>Choose companies, metrics and years, then “Build grid”.</Empty> : null}
    </div>
  );
}

const LINE_ITEMS = ["Revenue", "CostOfRevenue", "OperatingIncome", "NetIncome",
  "TotalAssets", "CurrentAssets", "CurrentLiabilities", "Debt", "CashFromOperations", "Capex"];

function MetricPicker({ catalog, selected, onToggle }:
  { catalog: string[]; selected: string[]; onToggle: (m: string) => void }) {
  // Show line-items first, then the derived catalog, de-duped.
  const all = [...new Set([...LINE_ITEMS, ...catalog])];
  return (
    <div className="flex gap-8 wrap">
      {all.map((m) => (
        <label key={m} className="mono" style={{ fontSize: 12, cursor: "pointer" }}>
          <input type="checkbox" checked={selected.includes(m)} onChange={() => onToggle(m)} /> {m}
        </label>
      ))}
    </div>
  );
}

function GridTable({ data, metrics }: { data: MetricsGridResponse; metrics: string[] }) {
  const [active, setActive] = useState<MetricCell | null>(null);

  // Pivot: rows = metric, columns = (ticker, fiscal_year). Value cells format by metric.
  const cols = useMemo(() => {
    const set = new Map<string, { ticker: string; fy: number }>();
    data.cells.forEach((c) => {
      const t = c.ticker || c.cik || "?";
      set.set(`${t}|${c.fiscal_year}`, { ticker: t, fy: c.fiscal_year });
    });
    return [...set.values()].sort((a, b) => a.ticker.localeCompare(b.ticker) || a.fy - b.fy);
  }, [data]);

  const lookup = useMemo(() => {
    const m = new Map<string, MetricCell>();
    data.cells.forEach((c) => m.set(`${c.metric}|${c.ticker || c.cik}|${c.fiscal_year}`, c));
    return m;
  }, [data]);

  if (!data.cells.length) return <Empty>No data for that selection (period may not be filed yet).</Empty>;

  const components = active?.component_fact_ids
    ? Object.entries(active.component_fact_ids as Record<string, unknown>)
    : [];

  return (
    <Card title={`Grid — ${data.fiscal_period}`} hint="click any value to trace it to its formula and source filing">
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              {cols.map((c) => <th key={`${c.ticker}${c.fy}`} className="mono">{c.ticker} FY{c.fy}</th>)}
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <tr key={metric}>
                <td className="mono">{metric}</td>
                {cols.map((c) => {
                  const cell = lookup.get(`${metric}|${c.ticker}|${c.fy}`);
                  const has = cell && cell.value != null;
                  return (
                    <td
                      key={`${c.ticker}${c.fy}`}
                      className="mono"
                      style={has ? { cursor: "pointer" } : undefined}
                      onClick={() => has && setActive(cell!)}
                      title={has ? "Trace this value" : undefined}
                    >
                      {fmt(metric, cell?.value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active && (
        <SlideOver
          title={<span><span className="mono">{active.metric}</span> · {active.ticker} FY{active.fiscal_year}</span>}
          onClose={() => setActive(null)}
        >
          <KV rows={[
            ["Value", <span className="mono">{fmt(active.metric, active.value)}</span>],
            ["Kind", active.kind],
            ["Unit", active.unit ?? "—"],
            ["Period", active.fiscal_period],
            ["Formula", active.formula ? <code className="mono">{active.formula}</code> : "—"],
          ]} />

          {components.length > 0 && (
            <>
              <h4 style={{ margin: "16px 0 6px", fontSize: 13 }}>Component facts</h4>
              <table>
                <thead><tr><th>Component</th><th>Fact id(s)</th></tr></thead>
                <tbody>
                  {components.map(([k, v]) => (
                    <tr key={k}>
                      <td className="mono">{k}</td>
                      <td className="mono">{Array.isArray(v) ? v.join(", ") : String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h4 style={{ margin: "16px 0 6px", fontSize: 13 }}>Source citation</h4>
          <Citation value={active.citation} />
        </SlideOver>
      )}
    </Card>
  );
}
