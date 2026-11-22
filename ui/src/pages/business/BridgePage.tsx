import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { BridgeComponent } from "../../lib/types";
import { Card, Empty, KV, SlideOver, Tile } from "../../components/ui";
import { Waterfall, type BridgeItem } from "../../components/charts";

const DIMENSIONS = ["segment", "product", "geography"];
const PERIODS = ["FY", "Q1", "Q2", "Q3", "Q4"];

function money(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}k`;
  return `${sign}$${a.toFixed(0)}`;
}
const signed = (v?: number | null) => (v != null && v >= 0 ? `+${money(v)}` : money(v));

export default function BridgePage() {
  const [sp, setSp] = useSearchParams();
  const [company, setCompany] = useState(sp.get("company") ?? "NVDA");
  const [concept, setConcept] = useState(sp.get("concept") ?? "Revenue");
  const [dimension, setDimension] = useState(sp.get("dimension") ?? "segment");
  const [fy, setFy] = useState(sp.get("fy") ?? "2025");
  const [period, setPeriod] = useState(sp.get("period") ?? "FY");
  const [active, setActive] = useState<BridgeComponent | null>(null);

  const [submitted, setSubmitted] = useState<null | {
    company: string; concept: string; fiscal_year: number; dimension: string; fiscal_period: string;
  }>(sp.get("company")
    ? { company: sp.get("company")!, concept, fiscal_year: Number(fy), dimension, fiscal_period: period }
    : null);

  const bridge = useQuery({
    queryKey: ["bridge", submitted],
    queryFn: () => api.factsBridge(submitted!),
    enabled: !!submitted,
  });

  function run() {
    const fiscal_year = Number(fy);
    if (!company.trim() || Number.isNaN(fiscal_year)) return;
    const s = { company: company.trim(), concept: concept.trim() || "Revenue", fiscal_year, dimension, fiscal_period: period };
    setSubmitted(s);
    setSp({ company: s.company, concept: s.concept, dimension, fy: String(fiscal_year), period });
  }

  const data = bridge.data;
  const items: BridgeItem[] = useMemo(() => {
    if (!data) return [];
    const priorYr = data.fiscal_year - 1;
    const out: BridgeItem[] = [
      { label: `${data.concept} FY${priorYr}`, value: data.total_prior ?? 0, kind: "total" },
    ];
    [...(data.components ?? [])]
      .filter((c) => c.change != null && c.change !== 0)
      .sort((a, b) => Math.abs(b.change ?? 0) - Math.abs(a.change ?? 0))
      .forEach((c) => out.push({
        label: c.member ?? "—",
        value: c.change ?? 0,
        kind: "delta",
        onClick: () => setActive(c),
      }));
    if (data.unexplained != null && Math.abs(data.unexplained) > 0) {
      out.push({ label: "Unexplained", value: data.unexplained, kind: "delta" });
    }
    out.push({ label: `${data.concept} FY${data.fiscal_year}`, value: data.total_current ?? 0, kind: "total" });
    return out;
  }, [data]);

  return (
    <div>
      <h1 className="page-title">Variance Bridge</h1>
      <p className="page-sub">
        Decompose a metric's year-over-year change into its segment, product or geographic drivers —
        deterministic, cited, no LLM. Each bar traces to its underlying facts.
      </p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <label className="dim">Company</label>
          <input className="text-input mono" style={{ width: 120 }} value={company}
            onChange={(e) => setCompany(e.target.value)} placeholder="NVDA" />
          <label className="dim">Concept</label>
          <input className="text-input mono" style={{ width: 130 }} value={concept}
            onChange={(e) => setConcept(e.target.value)} placeholder="Revenue" />
          <label className="dim">By</label>
          <select className="text-input" value={dimension} onChange={(e) => setDimension(e.target.value)}>
            {DIMENSIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <label className="dim">FY</label>
          <input className="text-input mono" style={{ width: 80 }} value={fy}
            onChange={(e) => setFy(e.target.value)} placeholder="2025" />
          <label className="dim">Period</label>
          <select className="text-input" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn" onClick={run}>Build bridge</button>
        </div>
      </Card>

      {bridge.isFetching ? <Empty>Loading…</Empty> : null}
      {bridge.isError ? <Empty>Couldn't build the bridge. Check the company and concept, then try again.</Empty> : null}
      {!submitted ? <Empty>Pick a company, concept and dimension, then “Build bridge”.</Empty> : null}

      {data && (
        <>
          <div className="grid cols-4">
            <Tile label={`Prior (FY${data.fiscal_year - 1})`} value={money(data.total_prior)} />
            <Tile label={`Current (FY${data.fiscal_year})`} value={money(data.total_current)} />
            <Tile label="Total change" value={signed(data.total_change)} />
            <Tile label="Unexplained" value={signed(data.unexplained)} />
          </div>

          <Card title={`${data.concept} bridge — ${data.company.ticker ?? data.company.cik} · by ${data.dimension}`}
            hint="click a driver bar to see its cited facts">
            {items.length > 2 ? (
              <Waterfall items={items} valueFmt={(v) => money(Number(v))} height={300} />
            ) : (
              <Empty>No dimensional breakdown available for this selection.</Empty>
            )}
          </Card>

          <Card title="Drivers" hint="per-member prior → current change">
            {data.components.length ? (
              <table>
                <thead>
                  <tr><th>Member</th><th>Prior</th><th>Current</th><th>Change</th><th>% change</th></tr>
                </thead>
                <tbody>
                  {[...data.components].sort((a, b) => Math.abs(b.change ?? 0) - Math.abs(a.change ?? 0)).map((c, i) => (
                    <tr key={i} className="click" onClick={() => setActive(c)}>
                      <td>{c.member ?? "—"}</td>
                      <td className="mono">{money(c.prior)}</td>
                      <td className="mono">{money(c.current)}</td>
                      <td className="mono" style={{ color: (c.change ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>{signed(c.change)}</td>
                      <td className="mono">{c.pct_change != null ? `${(c.pct_change * 100).toFixed(1)}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty>No components.</Empty>}
          </Card>
        </>
      )}

      {active && (
        <SlideOver title={<span>Driver · {active.member ?? "—"}</span>} onClose={() => setActive(null)}>
          <KV rows={[
            ["Member", active.member ?? "—"],
            ["Prior", <span className="mono">{money(active.prior)}</span>],
            ["Current", <span className="mono">{money(active.current)}</span>],
            ["Change", <span className="mono">{signed(active.change)}</span>],
            ["% change", active.pct_change != null ? `${(active.pct_change * 100).toFixed(1)}%` : "—"],
            ["Fact id (current)", active.fact_id_current != null ? <span className="mono">{active.fact_id_current}</span> : "—"],
            ["Fact id (prior)", active.fact_id_prior != null ? <span className="mono">{active.fact_id_prior}</span> : "—"],
          ]} />
          <p className="dim mt-8" style={{ fontSize: 12 }}>
            Fact ids are traceable in Numeric Lineage (reverse audit) on the Technical console.
          </p>
        </SlideOver>
      )}
    </div>
  );
}
