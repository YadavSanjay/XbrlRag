import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { TraceEvent } from "../../lib/types";
import { Badge, Card, ms } from "../../components/ui";
import { isFallbackDecision } from "../../components/EventStream";

type NodeDef = { id: string; label: string; file: string; role: string };

// Flow lane: the request's path through the XBRL hybrid pipeline.
const FLOW: NodeDef[] = [
  { id: "decomposer", label: "Query Decomposer", file: "query_decomposer.py", role: "Split a compound question into ordered sub-queries (else one passthrough)." },
  { id: "intent_router", label: "Intent Router", file: "intent_router.py", role: "Classify intent; resolve company→CIK (ticker, alias, fuzzy name), period→FY, concepts." },
  { id: "branch_dispatcher", label: "Branch Dispatcher", file: "branch_dispatcher.py", role: "Route to fact-direct / narrative / plan-generator." },
  { id: "plan_generator", label: "Plan Generator", file: "plan_generator.py", role: "LLM builds a typed plan (lookup/compute/screen/breakdown/narrative)." },
  { id: "plan_executor", label: "Plan Executor", file: "plan_executor.py", role: "Execute steps; narrow OK-only fallbacks (sub-family, hint split, intersection, derived)." },
  { id: "synthesizer", label: "Synthesizer", file: "llm_xbrl_synthesizer.py", role: "Narrate facts + chunks into the answer with per-statement citations." },
  { id: "verifier", label: "Faithfulness Verifier", file: "llm_xbrl_faithfulness_verifier.py", role: "Per-statement check; drop unsupported claims." },
];

// Shared services that pulse when relevant events fire.
const SHARED: NodeDef[] = [
  { id: "llm_gateway", label: "LLM Gateway", file: "src/llm/gateway/", role: "All LLM calls: retry, cost, budget, escalation, prompt registry." },
  { id: "fact_store", label: "Fact Store", file: "xbrl_db (Postgres MVs)", role: "Deterministic SQL: lookup, cross-filer screen, single-filer breakdown." },
  { id: "retriever", label: "Retriever / Vector DB", file: "xbrl_narrative_retriever.py · Qdrant", role: "Embed → hybrid (dense+sparse) search → rerank." },
];

// Stage name (from event.stage) → flow node id.
const STAGE_NODE: Record<string, string> = {
  decomposer: "decomposer",
  intent_router: "intent_router",
  plan_generator: "plan_generator",
  plan_exec: "plan_executor",
  synthesizer: "synthesizer",
  verifier: "verifier",
};
const NODE_STAGE: Record<string, string> = Object.fromEntries(
  Object.entries(STAGE_NODE).map(([stage, node]) => [node, stage]),
);

/** Node ids that a single event lights up. */
function nodesForEvent(e: TraceEvent): string[] {
  const out = new Set<string>();
  const node = e.stage ? STAGE_NODE[e.stage] : undefined;
  if (node) out.add(node);
  const p = e.payload ?? {};
  if (e.kind === "subquery") out.add("decomposer");
  if (e.kind === "decision") {
    // An executor fallback is the executor's decision, not a routing one.
    if (isFallbackDecision(e)) { out.add("plan_executor"); out.add("fact_store"); }
    else { out.add("branch_dispatcher"); out.add("intent_router"); }
  }
  if (e.kind === "llm_call" || e.kind === "model_escalation") out.add("llm_gateway");
  if (e.kind === "sql_query") out.add("fact_store");
  if (e.kind === "numeric_lineage") {
    out.add("plan_executor");
    if (p.op === "lookup") out.add("fact_store");
  }
  return [...out];
}

export default function ArchitecturePage() {
  const [params] = useSearchParams();
  const [queryId, setQueryId] = useState(params.get("q") ?? "");
  const [active, setActive] = useState(params.get("q") ?? "");

  const { data, isLoading } = useQuery({
    queryKey: ["trace", active],
    queryFn: () => api.trace(active),
    enabled: !!active,
  });

  const header = data?.header ?? null;
  // Only events that touch a node drive the replay.
  const events = useMemo(
    () => (data?.events ?? []).filter((e) => nodesForEvent(e).length > 0),
    [data],
  );
  const stageLat = header?.stage_latency_ms ?? {};

  // ---- replay state ----
  const [cursor, setCursor] = useState(-1); // -1 = not started
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);

  // Reset + autoplay whenever a new trace loads.
  useEffect(() => {
    setCursor(-1);
    setPlaying(events.length > 0);
  }, [events]);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setCursor((c) => {
        if (c + 1 >= events.length) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 750);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [playing, events.length]);

  const current = cursor >= 0 ? events[cursor] : null;
  const activeNodes = current ? new Set(nodesForEvent(current)) : new Set<string>();
  const visited = useMemo(() => {
    const s = new Set<string>();
    for (let i = 0; i <= cursor && i < events.length; i++) nodesForEvent(events[i]).forEach((n) => s.add(n));
    return s;
  }, [cursor, events]);

  function load() { setActive(queryId.trim()); }

  return (
    <div>
      <h1 className="page-title">Architecture Map</h1>
      <p className="page-sub">Pipeline components — load a query_id to replay a real request flowing through them.</p>

      <Card>
        <div className="flex gap-8 wrap items-center">
          <input className="text-input mono" style={{ width: 280 }} placeholder="query_id to replay"
            value={queryId} onChange={(e) => setQueryId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()} />
          <button className="btn" onClick={load}>Load trace</button>
          {active ? (
            <div className="replay-bar" style={{ flex: 1 }}>
              <button className="btn ghost" onClick={() => { setCursor(-1); setPlaying(true); }}>⟲ Restart</button>
              <button className="btn ghost" onClick={() => setPlaying((p) => !p)} disabled={!events.length}>
                {playing ? "⏸ Pause" : "▶ Play"}
              </button>
              <input className="scrub" type="range" min={-1} max={Math.max(events.length - 1, 0)}
                value={cursor} onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }} />
              <span className="dim mono">{cursor < 0 ? 0 : cursor + 1}/{events.length}</span>
            </div>
          ) : null}
        </div>
        {isLoading ? <p className="dim mt-8 mb-0">Loading trace…</p> : null}
        {active && !isLoading && !events.length ? (
          <p className="dim mt-8 mb-0">No replayable events for this query_id (not persisted, or DB unreachable).</p>
        ) : null}
        {current ? (
          <p className="mono mt-8 mb-0">
            <Badge tone="blue">{current.kind}</Badge>{" "}
            <span className="dim">{current.stage !== "-" ? current.stage : ""}</span>{" "}
            {summary(current)}
          </p>
        ) : null}
      </Card>

      {/* Flow lane */}
      <Card title="Request flow" hint={active ? "lit nodes = touched by this query; glow = current event" : "load a query_id to animate"}>
        <div className="lane">
          {FLOW.map((n, i) => (
            <FlowNode key={n.id} node={n} active={activeNodes.has(n.id)}
              visited={!active || visited.has(n.id)} latency={stageLat[NODE_STAGE[n.id] ?? n.id]} last={i === FLOW.length - 1} />
          ))}
        </div>
      </Card>

      {/* Shared services */}
      <Card title="Shared services" hint="pulse when the corresponding event fires">
        <div className="lane" style={{ gap: 12 }}>
          {SHARED.map((n) => (
            <div key={n.id} className={`node ${activeNodes.has(n.id) ? "active pulse" : (!active || visited.has(n.id)) ? "visited" : ""}`}>
              <div className="node-title">{n.label}</div>
              <div className="node-file">{n.file}</div>
              <div className="dim" style={{ fontSize: 11, marginTop: 6 }}>{n.role}</div>
            </div>
          ))}
        </div>
      </Card>

      {header ? (
        <Card title="This query">
          <div className="flex gap-8 wrap">
            {header.branch ? <Badge tone="blue">{header.branch}</Badge> : null}
            {header.intent ? <Badge tone="purple">{header.intent}</Badge> : null}
            <Badge tone="default">total {ms(header.total_latency_ms)}</Badge>
            <Badge tone="default">{header.llm_calls ?? 0} LLM calls</Badge>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function FlowNode({ node, active, visited, latency, last }: {
  node: NodeDef; active: boolean; visited: boolean; latency?: number; last: boolean;
}) {
  return (
    <>
      <div className={`node ${active ? "active" : visited ? "visited" : ""}`} title={node.role}>
        <div className="node-title">{node.label}</div>
        <div className="node-file">{node.file}</div>
        {latency != null ? <div className="node-lat"><Badge tone="green">{ms(latency)}</Badge></div> : null}
      </div>
      {!last ? <div className="arrow">→</div> : null}
    </>
  );
}

function summary(e: TraceEvent): string {
  const p = (e.payload ?? {}) as Record<string, unknown>;
  switch (e.kind) {
    case "llm_call": return `${p.model ?? ""} · ${p.latency_ms ?? "?"}ms`;
    case "decision":
      return isFallbackDecision(e)
        ? `${String(p.decision)} → ${p.accepted ? "accepted" : "rejected"}`
        : `→ ${p.branch ?? ""}`;
    case "subquery": return `${p.id ?? ""} · ${p.query ?? ""}`;
    case "sql_query": return `${p.source ?? "sql"} · ${p.n_rows ?? "?"} rows`;
    case "numeric_lineage": return p.op === "compute" ? `${p.expr ?? ""} = ${p.result ?? ""}` : `${p.concept_family ?? ""} = ${p.value ?? ""}`;
    default: return "";
  }
}
