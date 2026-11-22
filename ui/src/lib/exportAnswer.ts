// Build a Markdown document from an answer payload and trigger a download.
import type { QueryResponse } from "./types";
import { answerParts, planStepFor } from "./answerModel";

export function answerToMarkdown(resp: QueryResponse): string {
  const a = resp.answer ?? {};
  const meta = a.metadata ?? {};
  const lines: string[] = [];

  lines.push(`# ${a.query ?? "XbrlRag answer"}`);
  lines.push("");
  if (resp.query_id) lines.push(`*query_id: \`${resp.query_id}\` · mode: ${resp.mode}*`);
  lines.push(`*status: ${meta.fully_answered ? "fully answered" : "partial / review"}${meta.degraded ? " (degraded fallback)" : ""}*`);
  lines.push("");

  lines.push("## Answer");
  lines.push(a.answer_summary || "(no summary)");
  lines.push("");

  const details = a.answer_details ?? [];
  if (details.length) {
    lines.push("## Supporting statements");
    details.forEach((d) => {
      const cites = [
        ...(d.fact_ids ?? []).map((f) => `fact ${f}`),
        ...(d.chunk_ids ?? []),
      ];
      lines.push(`- ${d.statement}${cites.length ? `  \n  _${cites.join(", ")}_` : ""}`);
    });
    lines.push("");
  }

  const unanswered = a.unanswered_parts ?? [];
  if (unanswered.length) {
    lines.push("## Not answered");
    unanswered.forEach((u) => lines.push(`- **${u.question_fragment}** — ${u.reason}`));
    lines.push("");
  }

  const parts = answerParts(a);
  if (parts.some((p) => p.steps.length)) {
    lines.push("## Numeric lineage");
    for (const p of parts) {
      if (p.id && p.steps.length) lines.push(`- **${p.id}** — ${p.query ?? ""}`);
      const indent = p.id ? "  " : "";
      p.steps.forEach((s) => {
        const tag = `${indent}- \`${s.step_id}\``;
        if (s.op === "compute") {
          const expr = s.expr ?? planStepFor(p, s.step_id)?.expr ?? "";
          const shown = s.status === "NOT_MEANINGFUL" ? "n/m" : s.value ?? "—";
          lines.push(`${tag} compute: \`${expr}\` = **${shown}** (${s.status ?? "?"})`);
        } else if (s.op === "lookup") {
          lines.push(`${tag} lookup: **${s.value ?? "—"} ${s.unit ?? ""}** (${s.status ?? "?"})`);
        } else if (s.op === "screen" || s.op === "breakdown") {
          lines.push(`${tag} ${s.op}: ${(s.rows ?? []).length} row(s) (${s.status ?? "?"})`);
          (s.rows ?? []).forEach((r) => {
            const who = s.op === "breakdown" ? r.member ?? r.label : `${r.rank ?? ""}. ${r.ticker ?? ""}`;
            lines.push(`${indent}  - ${who}: ${r.value ?? "—"} ${r.unit ?? ""}${r.fact_id ? ` _(fact ${r.fact_id})_` : ""}`);
          });
        }
      });
    }
    lines.push("");
  }

  lines.push("---");
  lines.push(`_Exported from XbrlRag UI._`);
  return lines.join("\n");
}

export function downloadMarkdown(resp: QueryResponse): void {
  const md = answerToMarkdown(resp);
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const slug = (resp.query_id ?? "answer").slice(0, 16);
  link.href = url;
  link.download = `xbrlrag-answer-${slug}.md`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
