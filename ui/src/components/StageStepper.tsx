import { useState, type ReactNode } from "react";
import type { AnswerPayload, PlanStep, StepResult } from "../lib/types";
import {
  answerParts,
  flaggedStatements,
  isDecomposed,
  planStepFor,
  stepDimensions,
  stepMetric,
  type AnswerPart,
} from "../lib/answerModel";
import { formatValue, prettyConcept } from "./sourcesRegistry";
import { Badge, Json, statusTone } from "./ui";

/**
 * The "glass box" — derives the explainability stages (A3) from the raw answer
 * payload (XbrlRagResponse / RagResponse). Each stage maps to a real pipeline
 * stage. A compound question is answered per sub-query, so every stage that walks
 * steps groups them by sub-query. Prompts + full timing live in the technical trace.
 */
export default function StageStepper({ answer }: { answer: AnswerPayload }) {
  const parts = answerParts(answer);
  const decomposed = isDecomposed(answer);
  const meta = answer.metadata ?? {};
  const chunks = answer.narrative_chunks ?? [];
  const retrieved = answer.retrieved_chunks ?? [];

  const count = (pred: (s: StepResult) => boolean) =>
    parts.reduce((n, p) => n + p.steps.filter(pred).length, 0);
  const nPlan = parts.reduce((n, p) => n + p.planSteps.length, 0);
  const nLookups = count((s) => s.op === "lookup");
  const nRanked = count((s) => s.op === "screen" || s.op === "breakdown");
  const nComputes = count((s) => s.op === "compute");
  const flagged = flaggedStatements(parts);
  const verified = parts.some((p) => p.faithfulness);

  return (
    <div className="stepper">
      <Step n={1} title="Query understanding" subtitle="decomposition, intent + resolved entities">
        <div className="flex gap-8 wrap">
          {decomposed ? <Badge tone="purple">compound: {parts.length} sub-queries</Badge> : null}
          {meta.branch ? <Badge tone="blue">branch: {String(meta.branch)}</Badge> : null}
          {meta.intent ? <Badge tone="purple">intent: {String(meta.intent)}</Badge> : null}
          {meta.degraded ? <Badge tone="amber">degraded fallback</Badge> : null}
        </div>
        {meta.dispatch_reason ? <p className="dim mt-8 mb-0">{String(meta.dispatch_reason)}</p> : null}
        <p className="dim mt-8 mb-0">
          Company and period resolution (exact ticker, alias, fuzzy name) is captured per
          stage in the technical trace.
        </p>
      </Step>

      <Step
        n={2}
        title="Plan / decomposition"
        subtitle={nPlan ? `${nPlan} step(s)${decomposed ? ` across ${parts.length} sub-queries` : ""}` : "no structured plan"}
        dim={!nPlan && !decomposed}
      >
        {decomposed ? <SubQueryList parts={parts} /> : null}
        {nPlan ? (
          <PerPart parts={parts} show={(p) => p.planSteps.length > 0}>
            {(p) => <PlanTable steps={p.planSteps} />}
          </PerPart>
        ) : (
          <p className="dim mb-0">This path retrieved narrative chunks directly (no numeric plan).</p>
        )}
      </Step>

      <Step
        n={3}
        title="Fact lookups (deterministic SQL)"
        subtitle={nLookups ? `${nLookups} fact(s)` : "none"}
        dim={!nLookups}
      >
        {nLookups ? (
          <PerPart parts={parts} show={(p) => p.steps.some((s) => s.op === "lookup")}>
            {(p) => (
              <div className="grid cols-2">
                {p.steps.filter((s) => s.op === "lookup").map((s) => (
                  <LookupCard key={s.step_id} s={s} plan={planStepFor(p, s.step_id)} />
                ))}
              </div>
            )}
          </PerPart>
        ) : (
          <p className="dim mb-0">No numeric fact lookups for this query.</p>
        )}
      </Step>

      <Step
        n={4}
        title="Rankings & breakdowns"
        subtitle={nRanked ? `${nRanked} step(s)` : "none"}
        dim={!nRanked}
      >
        {nRanked ? (
          <PerPart parts={parts} show={(p) => p.steps.some((s) => s.op === "screen" || s.op === "breakdown")}>
            {(p) =>
              p.steps
                .filter((s) => s.op === "screen" || s.op === "breakdown")
                .map((s) => <RankedStep key={s.step_id} s={s} plan={planStepFor(p, s.step_id)} />)
            }
          </PerPart>
        ) : (
          <p className="dim mb-0">
            No cross-filer screen or single-filer breakdown in this answer.
          </p>
        )}
      </Step>

      <Step
        n={5}
        title="Computation"
        subtitle={nComputes ? `${nComputes} expr(s)` : "none"}
        dim={!nComputes}
      >
        {nComputes ? (
          <PerPart parts={parts} show={(p) => p.steps.some((s) => s.op === "compute")}>
            {(p) =>
              p.steps.filter((s) => s.op === "compute").map((s) => (
                <ComputeCard key={s.step_id} s={s} plan={planStepFor(p, s.step_id)} bindings={p.execution?.bindings} />
              ))
            }
          </PerPart>
        ) : (
          <p className="dim mb-0">No derived/computed numbers.</p>
        )}
      </Step>

      <Step
        n={6}
        title="Retrieval"
        subtitle={`${chunks.length + retrieved.length} chunk(s)`}
        dim={chunks.length + retrieved.length === 0}
      >
        {chunks.length || retrieved.length ? (
          <ul className="mono dim" style={{ margin: 0, paddingLeft: 18 }}>
            {chunks.slice(0, 8).map((c, i) => (
              <li key={i}>{String((c as Record<string, unknown>).chunk_id ?? JSON.stringify(c).slice(0, 80))}</li>
            ))}
            {retrieved.slice(0, 8).map(([id, text], i) => (
              <li key={`r${i}`}>{id} — <span className="dim">{text.slice(0, 70)}…</span></li>
            ))}
          </ul>
        ) : (
          <p className="dim mb-0">No narrative chunks retrieved.</p>
        )}
      </Step>

      <Step n={7} title="Prompts used" subtitle="system + user prompts, prompt version" dim>
        <p className="dim mb-0">
          The exact prompts and their version hashes are recorded as <code className="mono">llm_call</code>{" "}
          events. Open the technical trace to inspect them.
        </p>
      </Step>

      <Step n={8} title="Synthesis & verification" subtitle="answer assembled + faithfulness-checked">
        {meta.notes ? <p className="mb-0">{String(meta.notes)}</p> : null}
        <div className="flex gap-8 mt-8 wrap">
          <Badge tone={meta.fully_answered ? "green" : "amber"}>
            {meta.fully_answered ? "fully answered" : "partially answered"}
          </Badge>
          <Badge tone="default">{(answer.answer_details ?? []).length} verified statement(s)</Badge>
          {verified ? (
            <Badge tone={flagged.some((f) => f.dropped) ? "amber" : "green"}>
              {flagged.filter((f) => f.dropped).length} dropped by the verifier
            </Badge>
          ) : null}
        </div>
        {flagged.length ? (
          <table className="mt-8">
            <thead>
              <tr>{decomposed ? <th>Part</th> : null}<th>Verdict</th><th>Statement</th><th>Why</th></tr>
            </thead>
            <tbody>
              {flagged.map((f, i) => (
                <tr key={i}>
                  {decomposed ? <td className="mono">{f.part}</td> : null}
                  <td>
                    <Badge tone={f.dropped ? "amber" : "default"}>
                      {f.dropped ? "dropped" : "kept · partial"}
                    </Badge>
                  </td>
                  <td>{f.statement}</td>
                  <td className="dim">{f.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Step>
    </div>
  );
}

/** Renders `children` once per part; headed by the sub-query when there are several. */
function PerPart({
  parts, show, children,
}: { parts: AnswerPart[]; show: (p: AnswerPart) => boolean; children: (p: AnswerPart) => ReactNode }) {
  const visible = parts.filter(show);
  if (parts.length === 1) return <>{visible.map((p) => <div key="only">{children(p)}</div>)}</>;
  return (
    <>
      {visible.map((p) => (
        <div key={p.id ?? "part"} className="mt-8">
          <div className="flex gap-8 items-center" style={{ marginBottom: 6 }}>
            <Badge tone="purple">{p.id}</Badge>
            <span className="dim">{p.query}</span>
          </div>
          {children(p)}
        </div>
      ))}
    </>
  );
}

function SubQueryList({ parts }: { parts: AnswerPart[] }) {
  return (
    <table style={{ marginBottom: 10 }}>
      <thead>
        <tr><th>Sub-query</th><th>Question</th><th>Depends on</th><th>Outcome</th></tr>
      </thead>
      <tbody>
        {parts.map((p) => (
          <tr key={p.id ?? "q"}>
            <td className="mono">{p.id}</td>
            <td>{p.query}</td>
            <td className="mono">{p.dependsOn.length ? p.dependsOn.join(", ") : "—"}</td>
            <td>
              <Badge tone={p.fullyAnswered ? "green" : "amber"}>
                {p.fullyAnswered ? "answered" : "partial"}
              </Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const OP_TONE: Record<string, "purple" | "blue" | "green" | "default"> = {
  compute: "purple",
  narrative: "blue",
  screen: "green",
  breakdown: "green",
};

function PlanTable({ steps }: { steps: PlanStep[] }) {
  return (
    <table>
      <thead>
        <tr><th>Step</th><th>Op</th><th>Metric / expr</th><th>Company · FY</th><th>Qualifiers</th></tr>
      </thead>
      <tbody>
        {steps.map((s) => {
          const dims = stepDimensions(s);
          const quals = [
            ...dims.map(([k, v]) => `${k}: ${v}`),
            s.axis ? `by ${s.axis}` : null,
            s.top_n ? `top ${s.top_n}${s.order ? ` ${s.order}` : ""}` : null,
          ].filter(Boolean);
          return (
            <tr key={s.id}>
              <td className="mono">{s.id}</td>
              <td><Badge tone={OP_TONE[s.op] ?? "default"}>{s.op}</Badge></td>
              <td className="mono">{s.expr ?? stepMetric(s) ?? "—"}</td>
              <td>{[s.ticker ?? (s.op === "screen" ? "all filers" : null), s.fy ? `FY${s.fy}` : null].filter(Boolean).join(" · ") || "—"}</td>
              <td className="dim">{quals.length ? quals.join(" · ") : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function LookupCard({ s, plan }: { s: StepResult; plan?: PlanStep }) {
  const metric = stepMetric(plan);
  const dims = stepDimensions(plan, s.dimensions);
  const intersection = !!s.derived_from?.length && !s.component_fact_ids;
  const derived = !!s.component_fact_ids;
  return (
    <div className="card mb-0">
      <div className="flex gap-8 items-center wrap">
        <span className="mono">{s.step_id}</span>
        <Badge tone={statusTone(s.status)}>{s.status ?? "?"}</Badge>
        {derived ? <Badge tone="purple">derived from inputs</Badge> : null}
        {intersection ? <Badge tone="purple">merged sibling hints</Badge> : null}
        {s.confidence != null ? <span className="dim">conf {s.confidence}</span> : null}
      </div>
      {metric || plan?.ticker ? (
        <div className="dim" style={{ marginTop: 4 }}>
          {[metric ? prettyConcept(metric) : null, plan?.ticker, plan?.fy ? `FY${plan.fy}` : null]
            .filter(Boolean)
            .join(" · ")}
          {dims.length ? ` · ${dims.map(([k, v]) => `${k}: ${v}`).join(" · ")}` : ""}
        </div>
      ) : null}
      <div className="value" style={{ fontSize: 22, margin: "6px 0" }}>
        {s.value != null ? Number(s.value).toLocaleString() : "—"}{" "}
        <span className="dim" style={{ fontSize: 13 }}>{s.unit ?? ""}</span>
      </div>
      {s.error ? <p className="dim mb-0">{s.error}</p> : null}
      {derived ? (
        <p className="dim mb-0" style={{ fontSize: 12 }}>
          Not tagged by the filer; computed from{" "}
          {Object.entries(s.component_fact_ids ?? {}).map(([k, v]) => `${k} (fact ${String(v)})`).join(" and ")}.
        </p>
      ) : null}
      {intersection ? (
        <p className="dim mb-0" style={{ fontSize: 12 }}>
          Executor fallback: steps {s.derived_from?.join(" + ")} found nothing alone; one fact
          matches both hints together.
        </p>
      ) : null}
      {s.citation ? <Json value={s.citation} /> : null}
    </div>
  );
}

function RankedStep({ s, plan }: { s: StepResult; plan?: PlanStep }) {
  const rows = s.rows ?? [];
  const isBreakdown = s.op === "breakdown";
  const metric = stepMetric(plan);
  const share = rows.some((r) => r.pct_of_total != null);
  const excluded = s.excluded ?? [];
  return (
    <div className="card mb-0" style={{ marginBottom: 8 }}>
      <div className="flex gap-8 items-center wrap">
        <span className="mono">{s.step_id}</span>
        <Badge tone="green">{s.op}</Badge>
        <Badge tone={statusTone(s.status)}>{s.status ?? "?"}</Badge>
        <span className="dim">
          {[
            metric ? prettyConcept(metric) : null,
            isBreakdown ? plan?.ticker : "across filers",
            isBreakdown && plan?.axis ? `by ${plan.axis}` : null,
            plan?.fy ? `FY${plan.fy}` : null,
          ].filter(Boolean).join(" · ")}
        </span>
      </div>
      {s.error ? <p className="dim mt-8 mb-0">{s.error}</p> : null}
      {rows.length ? (
        <table className="mt-8">
          <thead>
            <tr>
              {isBreakdown ? null : <th>#</th>}
              <th>{isBreakdown ? "Member" : "Company"}</th>
              <th className="num">Value</th>
              {share ? <th className="num">Share</th> : null}
              <th>Basis</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.fact_id ?? i}`}>
                {isBreakdown ? null : <td className="mono">{r.rank ?? i + 1}</td>}
                <td>{isBreakdown ? r.member ?? r.label : r.ticker}{!isBreakdown && r.label && r.label !== metric ? <span className="dim"> · {r.label}</span> : null}</td>
                <td className="num">{formatValue(r.value, r.unit)}</td>
                {share ? <td className="num">{r.pct_of_total != null ? `${(r.pct_of_total * 100).toFixed(1)}%` : "—"}</td> : null}
                <td className="dim mono" style={{ fontSize: 12 }}>
                  {r.formula ? `= ${r.formula}` : r.fact_id ? `fact ${r.fact_id}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {excluded.length ? (
        <p className="dim mt-8 mb-0" style={{ fontSize: 12 }}>
          Not ranked — no FY{plan?.fy ?? "?"} figure reported yet:{" "}
          {excluded
            .map((e) => `${e.ticker ?? "?"}${e.latest_fiscal_year ? ` (latest FY${e.latest_fiscal_year})` : ""}`)
            .join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function ComputeCard({
  s, plan, bindings,
}: { s: StepResult; plan?: PlanStep; bindings?: Record<string, number | null> }) {
  const expr = s.expr ?? plan?.expr ?? "expr";
  const notMeaningful = (s.status ?? "").toUpperCase() === "NOT_MEANINGFUL";
  // `bindings` is the whole plan's map; show only the operands this expression uses.
  const operands = new Set(expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? []);
  const own = bindings
    ? Object.fromEntries(Object.entries(bindings).filter(([k]) => operands.has(k)))
    : {};
  return (
    <div className="card mb-0" style={{ marginBottom: 8 }}>
      <div className="flex gap-8 items-center wrap">
        <span className="mono">{s.step_id}</span>
        <code className="mono">{expr}</code>{" = "}
        <strong>{notMeaningful ? "n/m" : s.value != null ? Number(s.value).toLocaleString() : "—"}</strong>
        <Badge tone={statusTone(s.status)}>{s.status ?? "?"}</Badge>
      </div>
      {s.error ? (
        <p className="dim mt-8 mb-0">
          {notMeaningful ? "Not meaningful: " : ""}
          {s.error}
        </p>
      ) : null}
      {Object.keys(own).length ? (
        <div className="dim mono mt-8" style={{ overflowWrap: "anywhere" }}>
          bindings: {JSON.stringify(own)}
        </div>
      ) : null}
    </div>
  );
}

function Step({
  n, title, subtitle, dim, children,
}: { n: number; title: string; subtitle?: string; dim?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(!dim);
  return (
    <div className={`step ${dim ? "dim-step" : ""}`}>
      <div className="step-head" onClick={() => setOpen((o) => !o)}>
        <span className="step-num">{n}.</span>
        <strong>{title}</strong>
        {subtitle ? <span className="dim">— {subtitle}</span> : null}
        <span className="spacer" />
        <span className="dim">{open ? "▾" : "▸"}</span>
      </div>
      {open ? <div className="step-body">{children}</div> : null}
    </div>
  );
}
