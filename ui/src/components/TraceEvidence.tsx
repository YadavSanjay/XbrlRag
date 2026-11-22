import { useState } from "react";
import type { TraceEvent, LlmCallEvent, SqlQueryEvent } from "../lib/types";
import { Badge, Card, ms, usd } from "./ui";

/**
 * Trace-page evidence panels, consistent with the MacroRAG Traces tab:
 *  - LlmCalls: every `llm_call` event, expandable to the exact System / User
 *    prompt and model Response, with tokens / cost / latency.
 *  - SqlQueries: every `sql_query` event — the exact XBRL DB query that ran.
 */

export function LlmCalls({ events }: { events: TraceEvent[] }) {
  const calls = events.filter((e) => e.kind === "llm_call");
  if (!calls.length) return null;
  const totalCost = calls.reduce((s, e) => s + (Number((e.payload as LlmCallEvent).cost_usd) || 0), 0);
  return (
    <Card
      title={`LLM calls (${calls.length})`}
      hint="exact system + user prompt and model response for each call, with token cost"
    >
      <div className="flex gap-8 wrap" style={{ marginBottom: 10 }}>
        <Badge tone="blue">total {usd(totalCost)}</Badge>
      </div>
      <div>
        {calls.map((e) => (
          <CallCard key={e.seq} seq={e.seq} stage={e.stage} p={e.payload as LlmCallEvent} />
        ))}
      </div>
    </Card>
  );
}

function CallCard({ seq, stage, p }: { seq: number; stage: string; p: LlmCallEvent }) {
  const [open, setOpen] = useState(false);
  const hasBody = !!(p.system || p.user || p.response);
  const reasoning = p.reasoning_tokens ? ` (${p.reasoning_tokens} reasoning)` : "";
  const tokens =
    p.prompt_tokens != null || p.completion_tokens != null
      ? `${p.prompt_tokens ?? "?"}→${p.completion_tokens ?? "?"} tok${reasoning}`
      : "";
  return (
    <div className="card mb-0" style={{ marginBottom: 8, padding: "10px 14px" }}>
      <div className="flex gap-8 items-center wrap" style={{ cursor: "pointer" }} onClick={() => setOpen((o) => !o)}>
        <span className="dim mono" style={{ width: 28 }}>{seq}</span>
        <Badge tone="blue">{p.stage ?? stage}</Badge>
        <code className="mono">{p.model ?? "?"}</code>
        {p.outcome === "error" ? <Badge tone="red">error</Badge> : null}
        <span className="spacer" />
        <span className="dim mono">{tokens}</span>
        <span className="mono">{usd(p.cost_usd)}</span>
        <span className="dim mono">{ms(p.latency_ms)}</span>
        <span className="dim">{open ? "▾" : "▸"}</span>
      </div>
      {open ? (
        <div className="mt-8">
          {!hasBody ? (
            <p className="dim mb-0" style={{ fontSize: 12 }}>
              Prompt/response capture is off for this call (only token/cost were recorded).
            </p>
          ) : (
            <>
              {p.system ? <PromptBlock label="System" text={p.system} /> : null}
              {p.user ? <PromptBlock label="User prompt" text={p.user} /> : null}
              {p.response ? <PromptBlock label="Response" text={p.response} /> : null}
              {(p.prompt_version || p.reasoning_effort) ? (
                <div className="dim mono mt-8" style={{ fontSize: 11 }}>
                  {p.prompt_version ? `prompt version: ${p.prompt_version}` : ""}
                  {p.prompt_version && p.reasoning_effort ? " · " : ""}
                  {p.reasoning_effort ? `reasoning effort: ${p.reasoning_effort}` : ""}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function SqlQueries({ events }: { events: TraceEvent[] }) {
  const queries = events.filter((e) => e.kind === "sql_query");
  if (!queries.length) return null;
  return (
    <Card title={`SQL queries (${queries.length})`} hint="the exact deterministic queries run against the XBRL fact store">
      <div>
        {queries.map((e) => (
          <SqlCard key={e.seq} seq={e.seq} p={e.payload as SqlQueryEvent} />
        ))}
      </div>
    </Card>
  );
}

function SqlCard({ seq, p }: { seq: number; p: SqlQueryEvent }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="card mb-0" style={{ marginBottom: 8, padding: "10px 14px" }}>
      <div className="flex gap-8 items-center wrap" style={{ cursor: "pointer" }} onClick={() => setOpen((o) => !o)}>
        <span className="dim mono" style={{ width: 28 }}>{seq}</span>
        <Badge tone="green">{p.source ?? "sql"}</Badge>
        <span className="spacer" />
        <span className="dim mono">{p.n_rows ?? "?"} rows</span>
        <span className="dim mono">{ms(p.latency_ms)}</span>
        <span className="dim">{open ? "▾" : "▸"}</span>
      </div>
      {open ? <pre className="prompt-pre mt-8">{p.sql ?? "(no sql captured)"}</pre> : null}
    </div>
  );
}

// entity_extractor.py resolution labels: an alias comes from the committed
// configs/entities/company_aliases.yaml; a fuzzy or unresolved match deserves a look.
const RESOLUTION_TONE: Record<string, "green" | "blue" | "amber"> = {
  exact_ticker: "green",
  alias: "blue",
  fuzzy_name: "amber",
  ambiguous: "amber",
  unresolved: "amber",
};

const FALLBACK_LABEL: Record<string, string> = {
  subfamily_fallback: "Sub-family → Revenue retry",
  glued_hint_split: "Glued \"X and Y\" hint split",
  intersection_fallback: "Sibling-hint intersection",
  derived_family_fallback: "Derived from components",
};

/**
 * How the query was understood and where the pipeline deviated from its plan:
 *  - entity resolution per sub-query (`intent_extraction`): each company mention and
 *    how it resolved (exact ticker, committed alias, fuzzy name), and the periods;
 *  - executor fallbacks (`decision` events with a `decision` key), accepted or not;
 *  - model escalations and budget halts.
 */
export function ResolutionAndFallbacks({ events }: { events: TraceEvent[] }) {
  const intents = events.filter((e) => e.kind === "intent_extraction");
  const fallbacks = events.filter((e) => e.kind === "decision" && typeof e.payload?.decision === "string");
  const escalations = events.filter((e) => e.kind === "model_escalation" || e.kind === "budget_halt");
  if (!intents.length && !fallbacks.length && !escalations.length) return null;
  return (
    <Card
      title="Resolution & fallbacks"
      hint="how companies and periods resolved, and every executor fallback or model escalation"
    >
      {intents.map((e) => {
        const p = e.payload as Record<string, unknown>;
        const companies = (p.companies as Array<Record<string, unknown>> | undefined) ?? [];
        const periods = (p.periods as Array<Record<string, unknown>> | undefined) ?? [];
        return (
          <div key={e.seq} style={{ marginBottom: 8 }}>
            <div className="flex gap-8 items-center wrap">
              <span className="dim mono" style={{ width: 28 }}>{e.seq}</span>
              <Badge tone="purple">{String(p.intent ?? "?")}</Badge>
              {p.fast_path ? <Badge tone="green">fast path</Badge> : null}
              {companies.map((c, i) => (
                <span key={i} className="mono">
                  “{String(c.mention ?? "?")}” → <strong>{String(c.ticker ?? "unresolved")}</strong>{" "}
                  <Badge tone={RESOLUTION_TONE[String(c.resolution)] ?? "default"}>
                    {String(c.resolution ?? "?")}
                  </Badge>
                </span>
              ))}
              {periods.length ? (
                <span className="dim mono">
                  {periods
                    .map((t) => `${String(t.mention ?? "")}→FY${String(t.fiscal_year ?? "?")}${t.fiscal_period && t.fiscal_period !== "FY" ? ` ${String(t.fiscal_period)}` : ""}${t.ambiguous ? " (ambiguous)" : ""}`)
                    .join(", ")}
                </span>
              ) : null}
            </div>
          </div>
        );
      })}

      {fallbacks.length ? (
        <table className="mt-8">
          <thead>
            <tr><th>#</th><th>Fallback</th><th>Step</th><th>Tried</th><th>Result</th></tr>
          </thead>
          <tbody>
            {fallbacks.map((e) => {
              const p = e.payload as Record<string, unknown>;
              const hints = p.hints as string[] | undefined;
              const dims = p.dimensions as Record<string, string> | undefined;
              const comps = p.components as Record<string, string> | undefined;
              const tried = hints?.length
                ? hints.join(" + ")
                : dims
                ? Object.entries(dims).map(([k, v]) => `${k}: ${v}`).join(", ")
                : comps
                ? Object.entries(comps).map(([k, v]) => `${k} ${v}`).join(", ")
                : "—";
              return (
                <tr key={e.seq}>
                  <td className="dim mono">{e.seq}</td>
                  <td>{FALLBACK_LABEL[String(p.decision)] ?? String(p.decision)}</td>
                  <td className="mono">{String(p.step_id ?? "—")}</td>
                  <td className="dim">{tried}</td>
                  <td>
                    <Badge tone={p.accepted ? "green" : "default"}>{p.accepted ? "accepted" : "rejected"}</Badge>{" "}
                    {p.status ? <span className="dim mono">{String(p.status)}</span> : null}
                    {p.fact_id != null ? <span className="dim mono"> · fact {String(p.fact_id)}</span> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}

      {escalations.map((e) => {
        const p = e.payload as Record<string, unknown>;
        return (
          <div key={e.seq} className="flex gap-8 items-center wrap mt-8">
            <span className="dim mono" style={{ width: 28 }}>{e.seq}</span>
            <Badge tone={e.kind === "budget_halt" ? "red" : "amber"}>{e.kind}</Badge>
            <span className="mono">
              {e.kind === "model_escalation"
                ? `${String(p.stage ?? "")}: ${String(p.from_model ?? "?")} → ${String(p.to_model ?? "?")} on ${String(p.reason ?? "?")}`
                : `${String(p.reason ?? "")} — ${String(p.detail ?? "")}`}
            </span>
          </div>
        );
      })}
    </Card>
  );
}

function PromptBlock({ label, text }: { label: string; text: string }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div className="dim" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>{label}</div>
      <pre className="prompt-pre">{text}</pre>
    </div>
  );
}
