import { useState } from "react";
import type { TraceEvent } from "../lib/types";
import { Badge, Json } from "./ui";

const kindTone: Record<string, "green" | "amber" | "red" | "blue" | "purple" | "default"> = {
  llm_call: "blue",
  sql_query: "green",
  decision: "purple",
  subquery: "purple",
  numeric_lineage: "green",
  retry: "amber",
  verifier_error: "red",
  intent_extraction: "blue",
  model_escalation: "amber",
  budget_halt: "red",
};

/** Executor fallbacks emit `decision` events keyed by `decision` (not `point`). */
export function isFallbackDecision(e: TraceEvent): boolean {
  return e.kind === "decision" && typeof e.payload?.decision === "string";
}

/** Ordered structured event stream (audit.query_event), each expandable. */
export default function EventStream({ events }: { events: TraceEvent[] }) {
  if (!events.length) return <p className="dim">No events recorded for this query.</p>;
  return (
    <div>
      {events.map((e) => <EventRow key={e.seq} e={e} />)}
    </div>
  );
}

function EventRow({ e }: { e: TraceEvent }) {
  const [open, setOpen] = useState(false);
  const p = e.payload ?? {};
  return (
    <div className="card mb-0" style={{ marginBottom: 8, padding: "10px 14px" }}>
      <div className="flex gap-8 items-center" style={{ cursor: "pointer" }} onClick={() => setOpen((o) => !o)}>
        <span className="dim mono" style={{ width: 28 }}>{e.seq}</span>
        <Badge tone={kindTone[e.kind] ?? "default"}>{e.kind}</Badge>
        <span className="dim mono">{e.stage}</span>
        <span className="dim mono" style={{ marginLeft: 6 }}>{summary(e.kind, p)}</span>
        <span className="spacer" />
        <span className="dim">{open ? "▾" : "▸"}</span>
      </div>
      {open ? <div className="mt-8"><Json value={p} /></div> : null}
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function summary(kind: string, p: any): string {
  switch (kind) {
    case "llm_call":
      return `${p.model ?? ""} · ${p.total_tokens ?? "?"}tok · $${p.cost_usd ?? 0} · ${p.latency_ms ?? "?"}ms`;
    case "sql_query":
      return `${p.source ?? "sql"} · ${p.n_rows ?? "?"} rows · ${p.latency_ms ?? "?"}ms`;
    case "subquery":
      return `${p.id ?? ""} ${p.depends_on?.length ? `⇐ ${p.depends_on.join(",")}` : ""} · ${p.query ?? ""}`;
    case "decision":
      // An executor fallback (sub-family → Revenue, glued-hint split, sibling
      // intersection, derived gross profit): kept only when `accepted`.
      if (typeof p.decision === "string") {
        const what = p.hints?.length ? ` [${p.hints.join(" + ")}]` : p.concept_family ? ` ${p.concept_family}` : "";
        return `${p.decision}${what} · ${p.step_id ?? ""} → ${p.accepted ? "accepted" : "rejected"}${p.status ? ` (${p.status})` : ""}`;
      }
      return `${p.point ?? ""} → ${p.branch ?? p.choice ?? ""} (${p.reason ?? ""})`;
    case "intent_extraction":
      return `${p.intent ?? ""} · ${(p.companies ?? [])
        .map((c: { mention?: string; ticker?: string; resolution?: string }) => `${c.mention ?? "?"}→${c.ticker ?? "?"} (${c.resolution ?? "?"})`)
        .join(", ")}`;
    case "model_escalation":
      return `${p.stage ?? ""} · ${p.from_model ?? ""} → ${p.to_model ?? ""} (${p.reason ?? ""})`;
    case "budget_halt":
      return `${p.reason ?? ""} · ${p.detail ?? ""}`;
    case "numeric_lineage":
      return p.op === "compute"
        ? `${p.expr ?? ""} = ${p.result ?? ""}`
        : `${p.concept_family ?? ""} = ${p.value ?? ""} ${p.unit ?? ""}`;
    case "retry":
      return `attempt ${p.attempt} · ${p.error_class ?? ""} · wait ${p.wait_ms ?? "?"}ms`;
    case "verifier_error":
      return String(p.status ?? p.error ?? "");
    default:
      return "";
  }
}
