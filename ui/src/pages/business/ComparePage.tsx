import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { TraceDetail, TraceEvent } from "../../lib/types";
import { Badge, Card, Empty, ms, usd } from "../../components/ui";

type Row = Record<string, unknown>;

/** Distinct `stage:prompt_version` pairs used in a trace's LLM calls. */
function promptVersions(events: TraceEvent[]): string[] {
  const set = new Set<string>();
  events.forEach((e) => {
    if (e.kind !== "llm_call") return;
    const p = e.payload ?? {};
    if (p.prompt_version) set.add(`${e.stage}:${String(p.prompt_version)}`);
  });
  return [...set].sort();
}

/** Numeric values keyed by concept (lookup) or expression (compute). */
function numericMap(items: Row[]): Map<string, number> {
  const m = new Map<string, number>();
  items.forEach((r) => {
    const key = r.op === "compute" ? `expr:${String(r.expr ?? r.step_id)}` : `${String(r.concept_family ?? r.step_id)}`;
    const raw = r.op === "compute" ? r.result : r.value;
    const n = Number(raw);
    if (!Number.isNaN(n)) m.set(key, n);
  });
  return m;
}

function num(n?: number): string {
  return n == null || Number.isNaN(n) ? "—" : n.toLocaleString();
}

export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const [a, setA] = useState(params.get("a") ?? "");
  const [b, setB] = useState(params.get("b") ?? "");
  const idA = params.get("a") ?? "";
  const idB = params.get("b") ?? "";

  const tA = useQuery({ queryKey: ["trace", idA], queryFn: () => api.trace(idA), enabled: !!idA });
  const tB = useQuery({ queryKey: ["trace", idB], queryFn: () => api.trace(idB), enabled: !!idB });
  const lA = useQuery({ queryKey: ["lineage", idA], queryFn: () => api.lineage(idA), enabled: !!idA });
  const lB = useQuery({ queryKey: ["lineage", idB], queryFn: () => api.lineage(idB), enabled: !!idB });

  function compare() {
    setParams({ a: a.trim(), b: b.trim() });
  }

  const numA = numericMap((lA.data?.items ?? []) as Row[]);
  const numB = numericMap((lB.data?.items ?? []) as Row[]);
  const numKeys = [...new Set([...numA.keys(), ...numB.keys()])].sort();

  return (
    <div>
      <h1 className="page-title">Compare</h1>
      <p className="page-sub">Two answers side by side — routing, economics, prompt versions, and numeric drift.</p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <input className="text-input mono" style={{ width: 240 }} placeholder="query_id A"
            value={a} onChange={(e) => setA(e.target.value)} />
          <span className="dim">vs</span>
          <input className="text-input mono" style={{ width: 240 }} placeholder="query_id B"
            value={b} onChange={(e) => setB(e.target.value)} />
          <button className="btn" onClick={compare} disabled={!a.trim() || !b.trim()}>Compare</button>
        </div>
      </Card>

      {!idA || !idB ? <Empty>Enter two query_ids (or pick two from History) to compare.</Empty> : null}

      {idA && idB ? (
        <>
          <div className="grid cols-2">
            <TraceSummary label="A" id={idA} trace={tA.data} />
            <TraceSummary label="B" id={idB} trace={tB.data} />
          </div>

          <Card title="Prompt versions" hint="distinct stage:prompt_version per query — spot prompt drift">
            <div className="grid cols-2">
              <PromptList events={tA.data?.events ?? []} />
              <PromptList events={tB.data?.events ?? []} />
            </div>
          </Card>

          <Card title="Numeric drift" hint="aligned by concept (lookups) / expression (computes)">
            {numKeys.length ? (
              <table>
                <thead><tr><th>Concept / expr</th><th>A</th><th>B</th><th>Δ (B−A)</th></tr></thead>
                <tbody>
                  {numKeys.map((k) => {
                    const va = numA.get(k); const vb = numB.get(k);
                    const delta = va != null && vb != null ? vb - va : undefined;
                    return (
                      <tr key={k}>
                        <td className="mono">{k}</td>
                        <td className="mono">{num(va)}</td>
                        <td className="mono">{num(vb)}</td>
                        <td className="mono">
                          {delta == null ? "—" : (
                            <Badge tone={delta === 0 ? "default" : delta > 0 ? "green" : "red"}>
                              {delta > 0 ? "+" : ""}{num(delta)}
                            </Badge>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <Empty>No numeric lineage to compare (narrative answers, or DB unreachable).</Empty>}
          </Card>
        </>
      ) : null}
    </div>
  );
}

function TraceSummary({ label, id, trace }: { label: string; id: string; trace?: TraceDetail }) {
  const h = trace?.header;
  return (
    <Card title={`Query ${label}`} hint={id}>
      {!h ? <Empty>Not persisted / DB unreachable.</Empty> : (
        <>
          <div style={{ marginBottom: 10 }}>{h.query}</div>
          <div className="flex gap-8 wrap">
            {h.branch ? <Badge tone="blue">{h.branch}</Badge> : null}
            {h.intent ? <Badge tone="purple">{h.intent}</Badge> : null}
            {h.degraded ? <Badge tone="amber">degraded</Badge> : null}
            <Badge tone={h.fully_answered ? "green" : "amber"}>{h.fully_answered ? "answered" : "partial"}</Badge>
          </div>
          <table className="mt-8">
            <tbody>
              <tr><td className="dim">Latency</td><td className="mono">{ms(h.total_latency_ms)}</td></tr>
              <tr><td className="dim">Cost</td><td className="mono">{usd(h.total_cost_usd)}</td></tr>
              <tr><td className="dim">LLM calls</td><td className="mono">{h.llm_calls ?? "—"}</td></tr>
              <tr><td className="dim">Retries</td><td className="mono">{h.total_retries ?? "—"}</td></tr>
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

function PromptList({ events }: { events: TraceEvent[] }) {
  const vs = promptVersions(events);
  return (
    <div>
      {vs.length ? vs.map((v) => <div key={v} className="mono" style={{ fontSize: 12 }}>{v}</div>)
        : <span className="dim">none recorded</span>}
    </div>
  );
}
