import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { ScreenRequest, ScreenRow } from "../../lib/types";
import { Card, Citation, Empty, KV, SlideOver } from "../../components/ui";
import { BarRank } from "../../components/charts";

type Kind = "total" | "geography" | "segment" | "derived";
const KINDS: { value: Kind; label: string }[] = [
  { value: "total", label: "Total metric" },
  { value: "geography", label: "By geography" },
  { value: "segment", label: "By segment / product" },
  { value: "derived", label: "Derived ratio" },
];
const PERIODS = ["FY", "Q1", "Q2", "Q3", "Q4"];

const money = (v?: number | null) => {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  return `${s}$${a.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

export default function ScreenPage() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<Kind>("total");
  const [concept, setConcept] = useState("Revenue");
  const [dimValue, setDimValue] = useState("China");
  const [derived, setDerived] = useState("GrossMarginPct");
  const [fy, setFy] = useState("2025");
  const [share, setShare] = useState(false); // pct_of_total for geo/segment
  const [order, setOrder] = useState("desc");
  const [topN, setTopN] = useState("10");
  const [period, setPeriod] = useState("FY");
  const [active, setActive] = useState<ScreenRow | null>(null);
  const [req, setReq] = useState<ScreenRequest | null>(null);

  const catalog = useQuery({ queryKey: ["metricsCatalog"], queryFn: () => api.metricsCatalog() });
  const derivedMetrics = (catalog.data?.items ?? []).map((i) => i.metric_name);

  const result = useQuery({
    queryKey: ["screen", req],
    queryFn: () => api.screen(req!),
    enabled: !!req,
  });

  const usesShare = (kind === "geography" || kind === "segment") && share;
  const metricKind = kind === "derived" ? `derived:${derived}` : usesShare ? "pct_of_total" : "value";

  function build() {
    const fiscal = Number(fy);
    if (Number.isNaN(fiscal)) return;
    const r: ScreenRequest = {
      concept_family: kind === "derived" ? "Revenue" : concept.trim() || "Revenue",
      fy: fiscal,
      dimension:
        kind === "geography" ? { geography: dimValue.trim() }
        : kind === "segment" ? { segment: dimValue.trim() }
        : null,
      metric_kind: metricKind,
      order,
      top_n: topN ? Number(topN) : null,
      fiscal_period: period,
    };
    setReq(r);
  }

  const data = result.data;
  const isPct = data?.metric_kind === "pct_of_total" || data?.metric_kind?.endsWith("Pct");
  const rowVal = (r: ScreenRow) => (data?.metric_kind === "pct_of_total" ? Number(r.pct_of_total) : Number(r.value));
  const fmtVal = (v?: number | null) => (v == null || Number.isNaN(v) ? "—" : isPct ? `${(v * 100).toFixed(1)}%` : money(v));

  const bars = useMemo(
    () =>
      (data?.rows ?? []).map((r) => ({
        label: r.ticker ?? r.cik ?? "—",
        value: rowVal(r) || 0,
        onClick: () => navigate(`/business/company/${encodeURIComponent(r.ticker ?? "")}`),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data],
  );

  return (
    <div>
      <h1 className="page-title">Ranking Studio</h1>
      <p className="page-sub">
        Screen every filer by a metric — “which companies have the highest…”. Deterministic cross-filer
        ranking, each row cited. No LLM.
      </p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <label className="dim">Screen</label>
          <select className="text-input" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>

          {kind === "derived" ? (
            <>
              <label className="dim">Ratio</label>
              <select className="text-input mono" value={derived} onChange={(e) => setDerived(e.target.value)}>
                {(derivedMetrics.length ? derivedMetrics : [derived]).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </>
          ) : (
            <>
              <label className="dim">Concept</label>
              <input className="text-input mono" style={{ width: 130 }} value={concept}
                onChange={(e) => setConcept(e.target.value)} placeholder="Revenue" />
            </>
          )}

          {(kind === "geography" || kind === "segment") && (
            <>
              <label className="dim">{kind === "geography" ? "Geography" : "Segment"}</label>
              <input className="text-input mono" style={{ width: 130 }} value={dimValue}
                onChange={(e) => setDimValue(e.target.value)} placeholder={kind === "geography" ? "China" : "Data Center"} />
              <label className="mono" style={{ fontSize: 12 }}>
                <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> as % of total
              </label>
            </>
          )}

          <label className="dim">FY</label>
          <input className="text-input mono" style={{ width: 70 }} value={fy} onChange={(e) => setFy(e.target.value)} />
          <label className="dim">Period</label>
          <select className="text-input" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <label className="dim">Order</label>
          <select className="text-input" value={order} onChange={(e) => setOrder(e.target.value)}>
            <option value="desc">highest first</option>
            <option value="asc">lowest first</option>
          </select>
          <label className="dim">Top</label>
          <input className="text-input mono" style={{ width: 60 }} value={topN} onChange={(e) => setTopN(e.target.value)} />
          <button className="btn" onClick={build}>Run screen</button>
        </div>
      </Card>

      {result.isFetching ? <Empty>Screening…</Empty> : null}
      {result.isError ? <Empty>The screen didn't run. Check the concept and year, then run it again.</Empty> : null}
      {!req ? <Empty>Configure a screen and hit “Run screen”.</Empty> : null}

      {data && data.status !== "OK" ? (
        <Empty>
          No rows ({data.status}). {data.resolution_notes?.[0] ?? ""}
        </Empty>
      ) : null}

      {data && data.status === "OK" && (
        <>
          <Card title={`Ranking — ${data.rows.length} filers`} hint="click a bar to open the company dashboard">
            <BarRank
              rows={bars}
              valueFmt={(v) => fmtVal(Number(v))}
              colorIndex={isPct ? 1 : 0}
              onRowClick={(r) => {
                const row = data.rows.find((x) => (x.ticker ?? x.cik) === r.label);
                if (row?.ticker) navigate(`/business/company/${encodeURIComponent(row.ticker)}`);
              }}
            />
          </Card>

          <Card title="Ranked filers" hint="click a row to see its cited fact">
            <table>
              <thead><tr><th>#</th><th>Ticker</th><th>FY</th><th>Value</th>{data.metric_kind === "value" ? <th>% of total</th> : null}<th></th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={`${r.ticker}${r.fiscal_year}`} className="click" onClick={() => setActive(r)}>
                    <td className="mono dim">{r.rank}</td>
                    <td className="mono">{r.ticker ?? r.cik}</td>
                    <td className="mono dim">FY{r.fiscal_year}</td>
                    <td className="mono">{fmtVal(rowVal(r))}</td>
                    {data.metric_kind === "value" ? <td className="mono dim">{r.pct_of_total != null ? `${(r.pct_of_total * 100).toFixed(1)}%` : "—"}</td> : null}
                    <td className="dim">cite ›</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.resolution_notes?.length ? (
              <p className="dim mt-8" style={{ fontSize: 12 }}>{data.resolution_notes.join(" · ")}</p>
            ) : null}
          </Card>
        </>
      )}

      {active && (
        <SlideOver title={<span>{active.ticker} · FY{active.fiscal_year}</span>} onClose={() => setActive(null)}>
          <KV rows={[
            ["Ticker", active.ticker ?? "—"],
            ["CIK", <span className="mono">{active.cik}</span>],
            ["Value", <span className="mono">{fmtVal(rowVal(active))}</span>],
            ["% of total", active.pct_of_total != null ? `${(active.pct_of_total * 100).toFixed(1)}%` : "—"],
            ["Label", active.label ?? "—"],
            ["Formula", active.formula ? <code className="mono">{active.formula}</code> : "—"],
          ]} />
          <div className="flex gap-8 mt-8" style={{ marginBottom: 12 }}>
            {active.ticker && <button className="btn ghost" onClick={() => navigate(`/business/company/${active.ticker}`)}>Open company →</button>}
          </div>
          <h4 style={{ margin: "8px 0 6px", fontSize: 13 }}>Citation</h4>
          <Citation value={active.citation} />
          {active.component_fact_ids && (
            <>
              <h4 style={{ margin: "16px 0 6px", fontSize: 13 }}>Component facts</h4>
              <pre className="json">{JSON.stringify(active.component_fact_ids, null, 2)}</pre>
            </>
          )}
        </SlideOver>
      )}
    </div>
  );
}
