import type { StreamEvent } from "../lib/types";
import { Badge } from "./ui";

// XBRL hybrid pipeline stage order (the stages that emit events). The decomposer
// runs once; a compound query then runs the remaining stages once per sub-query.
const STAGES = ["decomposer", "intent_router", "plan_generator", "plan_exec", "synthesizer", "verifier"];

const KIND_TONE: Record<string, "green" | "amber" | "red" | "blue" | "purple" | "default"> = {
  llm_call: "blue",
  decision: "purple",
  subquery: "purple",
  intent_extraction: "purple",
  sql_query: "green",
  model_escalation: "amber",
  numeric_lineage: "green",
  retry: "amber",
  verifier_error: "red",
  query_start: "default",
  query_summary: "green",
};

/** Live stage checklist + event ticker, driven by streamed SSE events. */
export default function LiveProgress({ events, done }: { events: StreamEvent[]; done: boolean }) {
  const seenStages = new Set(
    events.map((e) => e.stage).filter((s): s is string => !!s && s !== "-"),
  );
  // Current stage = stage of the most recent event that carries one.
  let current = "";
  for (let i = events.length - 1; i >= 0; i--) {
    const s = events[i].stage;
    if (s && s !== "-") { current = s; break; }
  }
  const summaryDone = done || events.some((e) => e.kind === "query_summary" || e.kind === "result");

  function status(stage: string): "done" | "active" | "pending" {
    if (summaryDone) return "done";
    if (stage === current) return "active";
    if (seenStages.has(stage)) return "done";
    // a later stage already started -> earlier stage is done
    const idx = STAGES.indexOf(stage);
    const curIdx = STAGES.indexOf(current);
    if (curIdx > -1 && idx < curIdx) return "done";
    return "pending";
  }

  const ticker = events.filter((e) => !["stream_open"].includes(e.kind)).slice(-12);
  const subqueries = events.filter((e) => e.kind === "subquery") as Array<
    StreamEvent & { id?: string; depends_on?: string[]; query?: string }
  >;
  const extraction = [...events].reverse().find((e) => e.kind === "intent_extraction") as
    | (StreamEvent & {
        intent?: string;
        companies?: Array<{ ticker?: string; mention?: string; resolution?: string }>;
        periods?: Array<{ mention?: string; fiscal_year?: number; ambiguous?: boolean }>;
        concepts?: string[];
      })
    | undefined;

  return (
    <>
    {extraction ? (
      <div className="card mb-0" style={{ marginBottom: 14 }}>
        <h3>Understood as</h3>
        <div className="flex gap-8 wrap items-center">
          {extraction.intent ? <Badge tone="purple">{extraction.intent}</Badge> : null}
          {(extraction.companies ?? []).map((c, i) => (
            <Badge key={`c${i}`} tone={c.ticker ? "green" : "amber"}>
              {c.ticker ?? c.mention}
              {!c.ticker ? " (unresolved)" : c.resolution === "alias" ? ` (alias of “${c.mention}”)` : ""}
            </Badge>
          ))}
          {(extraction.periods ?? []).map((p, i) => (
            <Badge key={`p${i}`} tone={p.fiscal_year ? "blue" : "amber"}>
              {p.fiscal_year ? `FY${p.fiscal_year}` : p.mention}
            </Badge>
          ))}
          {(extraction.concepts ?? []).map((c, i) => (
            <Badge key={`k${i}`}>{c}</Badge>
          ))}
          {!extraction.companies?.length && !extraction.periods?.length && !extraction.concepts?.length ? (
            <span className="dim">no specific company/period/concept detected</span>
          ) : null}
        </div>
      </div>
    ) : null}
    {subqueries.length > 1 ? (
      <div className="card mb-0" style={{ marginBottom: 14 }}>
        <h3>Compound question — split into {subqueries.length} sub-queries</h3>
        {subqueries.map((q) => (
          <div key={q.id} className="flex gap-8 items-center" style={{ marginBottom: 4 }}>
            <Badge tone="purple">{q.id}</Badge>
            <span>{q.query}</span>
            {q.depends_on?.length ? <span className="dim mono">⇐ {q.depends_on.join(", ")}</span> : null}
          </div>
        ))}
      </div>
    ) : null}
    <div className="grid cols-2">
      <div className="card mb-0">
        <h3>Pipeline progress</h3>
        <div className="stepper">
          {STAGES.map((s, i) => {
            const st = status(s);
            return (
              <div key={s} className={`step ${st === "pending" ? "dim-step" : ""}`}>
                <div className="step-head" style={{ cursor: "default" }}>
                  <span className="step-num">{i + 1}.</span>
                  <strong>{s}</strong>
                  <span className="spacer" />
                  {st === "done" ? <Badge tone="green">done</Badge>
                    : st === "active" ? <Badge tone="blue">running…</Badge>
                    : <span className="dim">pending</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card mb-0">
        <h3>Live events ({events.filter((e) => e.kind !== "stream_open").length})</h3>
        <div className="mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 6 }}>
          {ticker.length === 0 ? <span className="dim">waiting for first event…</span> : null}
          {ticker.map((e, i) => (
            <div key={i} className="flex gap-8 items-center">
              <Badge tone={KIND_TONE[e.kind] ?? "default"}>{e.kind}</Badge>
              <span className="dim">{e.stage && e.stage !== "-" ? e.stage : ""}</span>
              <span className="dim">{eventSummary(e)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
    </>
  );
}

function eventSummary(e: StreamEvent): string {
  const p = e as Record<string, unknown>;
  switch (e.kind) {
    case "llm_call":
      return `${p.model ?? ""} · ${p.total_tokens ?? "?"}tok · ${p.latency_ms ?? "?"}ms`;
    case "decision":
      return `${p.branch ?? p.choice ?? ""} (${p.reason ?? ""})`;
    case "numeric_lineage":
      return p.op === "compute" ? `${p.expr ?? ""} = ${p.result ?? ""}` : `${p.concept_family ?? ""} = ${p.value ?? ""}`;
    case "retry":
      return `attempt ${p.attempt ?? "?"} · ${p.error_class ?? ""}`;
    default:
      return "";
  }
}
