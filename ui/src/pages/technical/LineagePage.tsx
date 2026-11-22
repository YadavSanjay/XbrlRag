import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, Json, statusTone } from "../../components/ui";

type Row = Record<string, unknown>;

function asObj(v: unknown): Record<string, unknown> {
  if (v && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
  if (typeof v === "string") { try { const o = JSON.parse(v); return o && typeof o === "object" ? o : {}; } catch { return {}; } }
  return {};
}
function sourcesOf(row: Row): string[] {
  for (const k of ["bindings", "source_fact_ids", "source_steps"]) {
    const keys = Object.keys(asObj(row[k]));
    if (keys.length) return keys;
  }
  return [];
}

export default function LineagePage() {
  const [params] = useSearchParams();
  const [queryId, setQueryId] = useState(params.get("q") ?? "");
  const [active, setActive] = useState(params.get("q") ?? "");
  const [factId, setFactId] = useState("");
  const [activeFact, setActiveFact] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["lineage", active],
    queryFn: () => api.lineage(active),
    enabled: !!active,
  });
  const items = (data?.items ?? []) as Row[];

  const factQ = useQuery({
    queryKey: ["fact-queries", activeFact],
    queryFn: () => api.factQueries(activeFact),
    enabled: !!activeFact,
  });

  // Build the dependency graph: step_id -> row, and the set of roots (steps not
  // consumed by any compute step) which anchor the trees.
  const { byId, roots } = useMemo(() => {
    const map = new Map<string, Row>();
    items.forEach((r) => { const id = String(r.step_id ?? ""); if (id) map.set(id, r); });
    const referenced = new Set<string>();
    items.forEach((r) => sourcesOf(r).forEach((s) => referenced.add(s)));
    const rootRows = items.filter((r) => !referenced.has(String(r.step_id ?? "")));
    return { byId: map, roots: rootRows };
  }, [items]);

  function loadFact(id: string) { setFactId(id); setActiveFact(id); }

  return (
    <div>
      <h1 className="page-title">Numeric Lineage & Audit</h1>
      <p className="page-sub">Every number traced to its source facts and arithmetic — and the reverse: which answers used a fact.</p>

      <Card>
        <div className="flex gap-8 wrap">
          <input className="text-input mono" style={{ width: 280 }} placeholder="query_id"
            value={queryId} onChange={(e) => setQueryId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setActive(queryId.trim())} />
          <button className="btn" onClick={() => setActive(queryId.trim())}>Load lineage</button>
        </div>
      </Card>

      {isLoading ? <p className="dim">Loading…</p> : null}
      {active && !isLoading && !items.length ? <Empty>No lineage rows for this query_id.</Empty> : null}

      {items.length ? (
        <Card title="Lineage graph" hint="compute results resolve to their source steps and underlying facts; click a fact to reverse-audit it">
          <div className="lin-tree">
            {roots.map((r) => (
              <LinNode key={String(r.step_id)} stepId={String(r.step_id)} byId={byId} onFact={loadFact} seen={new Set()} />
            ))}
          </div>
        </Card>
      ) : null}

      {items.length ? (
        <Card title="Raw lineage rows">
          <details>
            <summary className="dim" style={{ cursor: "pointer" }}>{items.length} row(s) — expand</summary>
            <div className="mt-8"><Json value={items} /></div>
          </details>
        </Card>
      ) : null}

      {/* Reverse audit */}
      <Card title="Reverse audit — which answers used a fact" hint="audit.queries_for_fact(fact_id)">
        <div className="flex gap-8 wrap">
          <input className="text-input mono" style={{ width: 200 }} placeholder="fact_id"
            value={factId} onChange={(e) => setFactId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setActiveFact(factId.trim())} />
          <button className="btn" onClick={() => setActiveFact(factId.trim())} disabled={!factId.trim()}>Find queries</button>
        </div>

        {factQ.isLoading ? <p className="dim mt-8 mb-0">Loading…</p> : null}
        {activeFact && !factQ.isLoading && !(factQ.data?.items ?? []).length ? (
          <p className="dim mt-8 mb-0">No answers referenced fact {activeFact} (or DB unreachable).</p>
        ) : null}
        {(factQ.data?.items ?? []).length ? (
          <table className="mt-8">
            <thead><tr><th>query_id</th><th>When</th><th>Op</th><th>Step</th><th>Expr / result</th></tr></thead>
            <tbody>
              {(factQ.data!.items as Row[]).map((r, i) => (
                <tr key={i}>
                  <td><Link className="mono" to={`/tech/traces/${r.query_id}`}>{String(r.query_id ?? "").slice(0, 12)}</Link></td>
                  <td className="dim">{String(r.created_at ?? "")}</td>
                  <td>{String(r.op ?? "")}</td>
                  <td className="mono">{String(r.step_id ?? "")}</td>
                  <td className="mono">{String(r.expr ?? r.result ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>
    </div>
  );
}

function LinNode({ stepId, byId, onFact, seen }: {
  stepId: string; byId: Map<string, Row>; onFact: (id: string) => void; seen: Set<string>;
}) {
  const row = byId.get(stepId);
  if (!row) {
    return <div className="lin-row"><div className="lin-node">missing step <span className="mono">{stepId}</span></div></div>;
  }
  const op = String(row.op ?? "");
  const isCompute = op === "compute";
  const sources = isCompute ? sourcesOf(row) : [];
  const cycle = seen.has(stepId);
  const nextSeen = new Set(seen); nextSeen.add(stepId);

  return (
    <div className="lin-row">
      <div className={`lin-node ${op}`}>
        <Badge tone={op === "compute" ? "purple" : op === "lookup" ? "green" : "default"}>{op || "?"}</Badge>
        <span className="mono">{stepId}</span>
        <Badge tone={statusTone(String(row.status ?? ""))}>{String(row.status ?? "?")}</Badge>
        {isCompute
          ? <span className="lin-val">{String(row.expr ?? "")} = {String(row.result ?? "")}</span>
          : <span className="lin-val">{String(row.concept_family ?? "")} = {String(row.value ?? "")} {String(row.unit ?? "")}</span>}
        {row.fact_id ? <span className="lin-fact" title="reverse-audit this fact" onClick={() => onFact(String(row.fact_id))}>fact {String(row.fact_id)}</span> : null}
      </div>
      {sources.length && !cycle ? (
        <div className="lin-children">
          {sources.map((s) => <LinNode key={s} stepId={s} byId={byId} onFact={onFact} seen={nextSeen} />)}
        </div>
      ) : null}
    </div>
  );
}
