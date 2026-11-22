import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { BridgeComponent, FactPoint, MetricCell } from "../../lib/types";
import { Badge, Card, Citation, Empty, SlideOver, Tile } from "../../components/ui";
import { MixChart, Sparkline, TrendChart, type MixSlice } from "../../components/charts";

// KPI + trend metric set. Revenue/NetIncome/FreeCashFlow are dollars; *Pct are
// stored as fractions in mv_derived_metrics (×100 to display).
const METRICS = ["Revenue", "NetIncome", "GrossMarginPct", "OperatingMarginPct", "NetMarginPct", "FreeCashFlow"];
const money = (v?: number | null) => {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v), sign = v < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  return `${sign}$${a.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};
const pctFmt = (v?: number | null) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`);

function foldSlices(comps: BridgeComponent[], onClick?: (m: string) => void): MixSlice[] {
  const pos = comps
    .map((c) => ({ label: c.member ?? "—", value: Number(c.current) || 0 }))
    .filter((s) => s.value > 0)
    .sort((a, b) => b.value - a.value);
  if (pos.length <= 8) return pos.map((s) => ({ ...s, onClick: onClick ? () => onClick(s.label) : undefined }));
  const head = pos.slice(0, 7);
  const other = pos.slice(7).reduce((a, s) => a + s.value, 0);
  return [...head, { label: "Other", value: other }].map((s) => ({
    ...s,
    onClick: onClick && s.label !== "Other" ? () => onClick(s.label) : undefined,
  }));
}

export default function CompanyPage() {
  const { ticker: routeTicker } = useParams();
  const navigate = useNavigate();
  const [input, setInput] = useState(routeTicker ?? "");
  const [ticker, setTicker] = useState(routeTicker ?? "");
  const [drillMetric, setDrillMetric] = useState<string | null>(null);

  useEffect(() => {
    if (routeTicker && routeTicker !== ticker) {
      setTicker(routeTicker);
      setInput(routeTicker);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeTicker]);

  // Company typeahead (native datalist).
  const typeahead = useQuery({
    queryKey: ["companies", input],
    queryFn: () => api.companies(input),
    enabled: input.trim().length >= 1,
  });

  const coverage = useQuery({
    queryKey: ["coverage", ticker],
    queryFn: () => api.coverage(ticker),
    enabled: !!ticker,
  });
  const cov = coverage.data?.items?.[0];
  const years = useMemo(() => [...(cov?.fiscal_years ?? [])].sort((a, b) => a - b), [cov]);
  const latestFy = years[years.length - 1];

  const grid = useQuery({
    queryKey: ["companyGrid", ticker, years],
    queryFn: () => api.metricsGrid({ companies: [ticker], metrics: METRICS, fiscal_years: years, fiscal_period: "FY" }),
    enabled: !!ticker && years.length > 0,
  });

  const segBridge = useQuery({
    queryKey: ["companySeg", ticker, latestFy],
    queryFn: () => api.factsBridge({ company: ticker, concept: "Revenue", fiscal_year: latestFy, dimension: "segment" }),
    enabled: !!ticker && latestFy != null,
  });
  const geoBridge = useQuery({
    queryKey: ["companyGeo", ticker, latestFy],
    queryFn: () => api.factsBridge({ company: ticker, concept: "Revenue", fiscal_year: latestFy, dimension: "geography" }),
    enabled: !!ticker && latestFy != null,
  });

  // metric -> fy -> value
  const cells = grid.data?.cells ?? [];
  const byMetric = useMemo(() => {
    const m = new Map<string, Map<number, number>>();
    cells.forEach((c: MetricCell) => {
      if (c.value == null) return;
      if (!m.has(c.metric)) m.set(c.metric, new Map());
      m.get(c.metric)!.set(c.fiscal_year, c.value);
    });
    return m;
  }, [cells]);
  const seriesOf = (metric: string) => years.map((y) => byMetric.get(metric)?.get(y) ?? null);
  const latestOf = (metric: string) => (latestFy != null ? byMetric.get(metric)?.get(latestFy) ?? null : null);

  const revIncData = useMemo(
    () => years.map((y) => ({
      label: `FY${y}`,
      Revenue: byMetric.get("Revenue")?.get(y) ?? null,
      NetIncome: byMetric.get("NetIncome")?.get(y) ?? null,
    })),
    [years, byMetric],
  );
  const marginData = useMemo(
    () => years.map((y) => ({
      label: `FY${y}`,
      Gross: (byMetric.get("GrossMarginPct")?.get(y) ?? null) as number | null,
      Operating: (byMetric.get("OperatingMarginPct")?.get(y) ?? null) as number | null,
      Net: (byMetric.get("NetMarginPct")?.get(y) ?? null) as number | null,
    })).map((r) => ({
      label: r.label,
      Gross: r.Gross == null ? null : r.Gross * 100,
      Operating: r.Operating == null ? null : r.Operating * 100,
      Net: r.Net == null ? null : r.Net * 100,
    })),
    [years, byMetric],
  );

  function go() {
    const t = input.trim().toUpperCase();
    if (t) navigate(`/business/company/${encodeURIComponent(t)}`);
  }

  return (
    <div>
      <h1 className="page-title">Company Dashboard</h1>
      <p className="page-sub">
        A fundamentals overview for one filer — KPIs, multi-year trends and revenue mix — with drill-down
        to the audited facts behind every number.
      </p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <label className="dim">Company</label>
          <input
            className="text-input mono"
            style={{ width: 220 }}
            list="company-options"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && go()}
            placeholder="Ticker or name — e.g. NVDA"
          />
          <datalist id="company-options">
            {(typeahead.data?.items ?? []).map((c) => (
              <option key={c.cik} value={c.ticker}>{c.company_name}</option>
            ))}
          </datalist>
          <button className="btn" onClick={go}>Load</button>
          {cov && (
            <span className="dim" style={{ marginLeft: 8 }}>
              {cov.company_name} · <span className="mono">{cov.cik}</span> · FY {years[0]}–{latestFy} ·{" "}
              {cov.form_types.map((f) => <Badge key={f}>{f}</Badge>)}
            </span>
          )}
        </div>
      </Card>

      {!ticker ? <Empty>Enter a ticker to load its dashboard.</Empty> : null}
      {ticker && coverage.isFetching ? <Empty>Loading coverage…</Empty> : null}
      {ticker && coverage.data && !cov ? <Empty>No ingested coverage for “{ticker}”.</Empty> : null}

      {cov && (
        <>
          <div className="grid cols-4">
            <Tile label={`Revenue (FY${latestFy})`} value={
              <span className="flex gap-8 items-center">{money(latestOf("Revenue"))}<Sparkline values={seriesOf("Revenue")} colorIndex={0} /></span>
            } />
            <Tile label="Gross margin" value={
              <span className="flex gap-8 items-center">{pctFmt(latestOf("GrossMarginPct"))}<Sparkline values={seriesOf("GrossMarginPct")} colorIndex={1} /></span>
            } />
            <Tile label="Operating margin" value={
              <span className="flex gap-8 items-center">{pctFmt(latestOf("OperatingMarginPct"))}<Sparkline values={seriesOf("OperatingMarginPct")} colorIndex={2} /></span>
            } />
            <Tile label="Free cash flow" value={
              <span className="flex gap-8 items-center">{money(latestOf("FreeCashFlow"))}<Sparkline values={seriesOf("FreeCashFlow")} colorIndex={4} /></span>
            } />
          </div>

          <div className="grid cols-2" style={{ marginTop: 14 }}>
            <Card title="Revenue & net income" hint="click Revenue to trace the time series">
              {years.length ? (
                <TrendChart
                  data={revIncData}
                  xKey="label"
                  height={200}
                  yFmt={(v) => money(Number(v))}
                  series={[
                    { key: "Revenue", label: "Revenue", colorIndex: 0 },
                    { key: "NetIncome", label: "Net income", colorIndex: 1 },
                  ]}
                />
              ) : <Empty>No data.</Empty>}
              <div className="flex gap-8 mt-8">
                <button className="btn ghost" onClick={() => setDrillMetric("Revenue")}>Trace Revenue →</button>
                <button className="btn ghost" onClick={() => setDrillMetric("NetIncome")}>Trace Net income →</button>
              </div>
            </Card>

            <Card title="Margins" hint="gross / operating / net (%)">
              {marginData.some((r) => r.Gross != null || r.Net != null) ? (
                <TrendChart
                  data={marginData}
                  xKey="label"
                  height={200}
                  yFmt={(v) => `${Number(v).toFixed(0)}%`}
                  series={[
                    { key: "Gross", label: "Gross", colorIndex: 1 },
                    { key: "Operating", label: "Operating", colorIndex: 2 },
                    { key: "Net", label: "Net", colorIndex: 4 },
                  ]}
                />
              ) : <Empty>No margin data.</Empty>}
            </Card>
          </div>

          <div className="grid cols-2">
            <Card title={`Revenue by segment (FY${latestFy})`} hint="click a slice for its variance bridge">
              {segBridge.data && segBridge.data.components.length ? (
                <MixChart
                  slices={foldSlices(segBridge.data.components, (m) =>
                    navigate(`/business/bridge?company=${ticker}&concept=Revenue&dimension=segment&fy=${latestFy}&period=FY&member=${encodeURIComponent(m)}`))}
                  height={190}
                  valueFmt={(v) => money(Number(v))}
                />
              ) : <Empty>No segment breakdown.</Empty>}
            </Card>

            <Card title={`Revenue by geography (FY${latestFy})`} hint="from mv_geographic_facts">
              {geoBridge.data && geoBridge.data.components.length ? (
                <MixChart
                  slices={foldSlices(geoBridge.data.components)}
                  height={190}
                  valueFmt={(v) => money(Number(v))}
                />
              ) : <Empty>No geographic breakdown.</Empty>}
            </Card>
          </div>

          <PointInTimePanel ticker={ticker} years={years} />

          {drillMetric && (
            <MetricDrill ticker={ticker} metric={drillMetric} years={years} onClose={() => setDrillMetric(null)} />
          )}
        </>
      )}
    </div>
  );
}

// ---- point-in-time / restatement lookup ----
function PointInTimePanel({ ticker, years }: { ticker: string; years: number[] }) {
  const [concept, setConcept] = useState("Revenue");
  const [asOf, setAsOf] = useState("");
  const [original, setOriginal] = useState(false);
  const [req, setReq] = useState<null | { asOf: string; original: boolean; concept: string }>(null);
  const [cite, setCite] = useState<FactPoint | null>(null);

  const lookup = useQuery({
    queryKey: ["pit", ticker, concept, years, req],
    queryFn: () => api.factsLookup({
      company: ticker,
      concept,
      fiscal_years: years,
      as_of: req?.asOf || null,
      as_originally_reported: req?.original ?? false,
    }),
    enabled: !!req,
  });

  return (
    <Card title="Point-in-time / restatement" hint="value as first reported, or as it stood on a given filing date">
      <div className="flex gap-8 wrap items-center" style={{ marginBottom: 10 }}>
        <label className="dim">Concept</label>
        <input className="text-input mono" style={{ width: 150 }} value={concept} onChange={(e) => setConcept(e.target.value)} />
        <label className="dim">As of (date)</label>
        <input className="text-input mono" style={{ width: 140 }} type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        <label className="mono" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={original} onChange={(e) => setOriginal(e.target.checked)} /> as originally reported
        </label>
        <button className="btn" onClick={() => setReq({ asOf, original, concept })}>Look up</button>
      </div>
      {lookup.isFetching ? <Empty>Loading…</Empty> : null}
      {lookup.data && (
        lookup.data.unresolved ? <Empty>Company unresolved.</Empty> : (
          <table>
            <thead><tr><th>Fiscal year</th><th>Value</th><th>Status</th><th>Confidence</th><th></th></tr></thead>
            <tbody>
              {lookup.data.series.map((p) => (
                <tr key={p.fiscal_year}>
                  <td className="mono">FY{p.fiscal_year}</td>
                  <td className="mono">{money(p.value)} {p.unit ? <span className="dim">{p.unit}</span> : null}</td>
                  <td><Badge tone={p.status === "OK" ? "green" : p.status === "NOT_FOUND" ? "default" : "amber"}>{p.status}</Badge></td>
                  <td className="mono dim">{p.confidence != null ? p.confidence.toFixed(2) : "—"}</td>
                  <td>{p.citation ? <a href="#" onClick={(e) => { e.preventDefault(); setCite(p); }}>cite</a> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
      {cite && (
        <SlideOver title={<span>Citation · {concept} FY{cite.fiscal_year}</span>} onClose={() => setCite(null)}>
          {cite.notes && cite.notes.length ? (
            <p className="dim" style={{ fontSize: 12 }}>{cite.notes.join(" · ")}</p>
          ) : null}
          <Citation value={cite.citation} />
        </SlideOver>
      )}
    </Card>
  );
}

// ---- metric time-series drill (raw concept lookup) ----
function MetricDrill({ ticker, metric, years, onClose }: { ticker: string; metric: string; years: number[]; onClose: () => void }) {
  const lookup = useQuery({
    queryKey: ["metricDrill", ticker, metric, years],
    queryFn: () => api.factsLookup({ company: ticker, concept: metric, fiscal_years: years, fiscal_period: "FY" }),
  });
  const [cite, setCite] = useState<FactPoint | null>(null);
  return (
    <SlideOver title={<span><span className="mono">{metric}</span> · {ticker}</span>} onClose={onClose}>
      {lookup.isFetching ? <Empty>Loading…</Empty> : null}
      {lookup.data && !lookup.data.unresolved ? (
        <table>
          <thead><tr><th>FY</th><th>Value</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {lookup.data.series.map((p) => (
              <tr key={p.fiscal_year}>
                <td className="mono">FY{p.fiscal_year}</td>
                <td className="mono">{money(p.value)}</td>
                <td><Badge tone={p.status === "OK" ? "green" : "amber"}>{p.status}</Badge></td>
                <td>{p.citation ? <a href="#" onClick={(e) => { e.preventDefault(); setCite(p); }}>cite</a> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : lookup.data ? <Empty>Company unresolved.</Empty> : null}
      {cite && (
        <div style={{ marginTop: 12 }}>
          <h4 style={{ margin: "0 0 6px", fontSize: 13 }}>Citation · FY{cite.fiscal_year}</h4>
          <Citation value={cite.citation} />
        </div>
      )}
    </SlideOver>
  );
}
