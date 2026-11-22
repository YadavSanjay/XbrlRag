import type { AnswerPayload, StepRow } from "../lib/types";
import { answerParts, planStepFor, stepDimensions, stepMetric } from "../lib/answerModel";

/**
 * Pure citation/source join for an answer payload — no React, so it can be checked
 * in isolation. `answer_details[]` reference sources only by raw id; this builds a
 * numbered registry (first-appearance order → stable [1], [2], …) and joins each id
 * to readable evidence:
 *   - facts  → the executed step that produced the id, in any sub-query: a lookup's
 *              `citation`, a screen/breakdown row (whose `fact_id` may be synthetic,
 *              e.g. "FreeCashFlow:<cik>:<fy>" for a derived metric), or an input of a
 *              derived lookup. The step's plan supplies what the citation itself no
 *              longer carries (metric, filer, year, dimension hints).
 *   - chunks → retrieved text (`retrieved_chunks` pairs / `narrative_chunks`)
 */

/** A fact made readable: what it is (from the plan/step) + where it is (citation). */
export interface FactInfo {
  factId: string;
  metric?: string | null;
  label?: string | null;
  ticker?: string | null;
  cik?: string | null;
  fiscalYear?: number | null;
  value?: number | null;
  unit?: string | null;
  dimensions?: Array<[string, string]>;
  rank?: number | null;
  pctOfTotal?: number | null;
  formula?: string | null;
  components?: Record<string, unknown> | null;
  /** This id is an input of a derived figure (e.g. gross profit = revenue − cost). */
  componentOf?: string | null;
  /** The fact is one row of a `screen` or `breakdown` step. */
  rowOf?: string | null;
  /** The cited id is a plan step, not a fact: what that step returned. */
  stepOutcome?: { op: string | null; status: string | null; error: string | null } | null;
  accessionNo?: string | null;
  sectionPath?: string | null;
  htmlAnchor?: string | null;
  citation?: Record<string, unknown> | null;
}

export interface AnswerSource {
  n: number;
  kind: "fact" | "chunk";
  id: string;
  fact?: FactInfo | null; // facts
  text?: string | null; // chunks
}

export interface AnswerSourcesResult {
  sources: AnswerSource[];
  /** answer_details index → ordered source numbers backing that statement. */
  perStatement: number[][];
}

function str(v: unknown): string | null {
  return v == null || v === "" ? null : String(v);
}

function fromCitation(c?: Record<string, unknown> | null): Partial<FactInfo> {
  if (!c) return {};
  return {
    accessionNo: str(c.accession_no),
    sectionPath: str(c.section_path) ?? str(c.statement),
    htmlAnchor: str(c.html_anchor),
    citation: c,
  };
}

/** fact id → readable fact, across every sub-query's executed steps. */
export function indexFacts(a: AnswerPayload): Map<string, FactInfo> {
  const facts = new Map<string, FactInfo>();
  const put = (info: FactInfo) => {
    if (info.factId && !facts.has(info.factId)) facts.set(info.factId, info);
  };

  for (const part of answerParts(a)) {
    for (const s of part.steps) {
      const plan = planStepFor(part, s.step_id);
      const metric = stepMetric(plan);
      const base = {
        metric,
        ticker: str(plan?.ticker),
        cik: str(plan?.cik),
        fiscalYear: typeof plan?.fy === "number" ? plan.fy : null,
        dimensions: stepDimensions(plan, s.dimensions),
      };

      const fid = str(s.citation?.fact_id);
      if (fid) {
        put({ factId: fid, ...base, value: s.value, unit: s.unit, ...fromCitation(s.citation) });
      }

      // A derived lookup has no single fact; its audited inputs are named per key.
      for (const [key, id] of Object.entries(s.component_fact_ids ?? {})) {
        const cid = str(id);
        if (cid) put({ factId: cid, ...base, label: key, componentOf: metric ?? s.step_id ?? null });
      }

      for (const row of s.rows ?? []) put(rowFact(row, base, s.op));
    }
  }

  // The synthesizer sometimes cites a step id where a fact id belongs, typically to
  // report a step that found nothing. Resolve it to that step's outcome, last, so a
  // real fact id always wins.
  for (const part of answerParts(a)) {
    for (const s of part.steps) {
      const sid = str(s.step_id);
      if (!sid || facts.has(sid)) continue;
      const plan = planStepFor(part, sid);
      put({
        factId: sid,
        metric: stepMetric(plan),
        ticker: str(plan?.ticker),
        cik: str(plan?.cik),
        fiscalYear: typeof plan?.fy === "number" ? plan.fy : null,
        dimensions: stepDimensions(plan, s.dimensions),
        value: s.value,
        unit: s.unit,
        stepOutcome: { op: s.op ?? null, status: s.status ?? null, error: s.error ?? null },
        ...fromCitation(s.citation),
      });
    }
  }
  return facts;
}

function rowFact(
  row: StepRow,
  base: { metric: string | null; ticker: string | null; cik: string | null; fiscalYear: number | null },
  op?: string,
): FactInfo {
  const cite = fromCitation(row.citation);
  const factId = str(row.fact_id) ?? str(row.citation?.fact_id) ?? "";
  return {
    factId,
    metric: base.metric ?? str(row.label),
    // A breakdown row's label is the member of the filer's partition; a screen row's
    // is the metric or segment it ranked.
    label: str(row.member) ?? str(row.label),
    rowOf: op ?? null,
    ticker: str(row.ticker) ?? base.ticker,
    cik: str(row.cik) ?? base.cik,
    fiscalYear: typeof row.fiscal_year === "number" ? row.fiscal_year : base.fiscalYear,
    value: row.value,
    unit: row.unit,
    rank: row.rank,
    pctOfTotal: row.pct_of_total,
    formula: row.formula,
    components: row.component_fact_ids ?? null,
    ...cite,
  };
}

export function buildAnswerSources(a: AnswerPayload): AnswerSourcesResult {
  const details = a.answer_details ?? [];
  const facts = indexFacts(a);

  // chunk_id → excerpt text
  const chunkText = new Map<string, string>();
  for (const pair of a.retrieved_chunks ?? []) {
    const [id, text] = pair;
    if (id != null && text != null) chunkText.set(String(id), text);
  }
  for (const ch of a.narrative_chunks ?? []) {
    const rec = ch as Record<string, unknown>;
    const id = rec.chunk_id;
    if (id == null) continue;
    const t = pickText(rec);
    if (t && !chunkText.has(String(id))) chunkText.set(String(id), t);
  }

  const byKey = new Map<string, AnswerSource>();
  const sources: AnswerSource[] = [];

  function register(kind: "fact" | "chunk", rawId: string): AnswerSource {
    const key = `${kind}:${rawId}`;
    const existing = byKey.get(key);
    if (existing) return existing;
    const src: AnswerSource = {
      n: sources.length + 1,
      kind,
      id: rawId,
      fact: kind === "fact" ? facts.get(rawId) ?? null : null,
      text: kind === "chunk" ? chunkText.get(rawId) ?? null : null,
    };
    byKey.set(key, src);
    sources.push(src);
    return src;
  }

  const perStatement: number[][] = details.map((d) => {
    const nums: number[] = [];
    for (const f of d.fact_ids ?? []) nums.push(register("fact", String(f)).n);
    for (const c of d.chunk_ids ?? []) nums.push(register("chunk", String(c)).n);
    return nums;
  });

  return { sources, perStatement };
}

// ---- presentational formatting (pure) -----------------------------------

function pickText(rec: Record<string, unknown>): string | null {
  for (const k of ["text", "content", "chunk_text", "snippet", "body"]) {
    const v = rec[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return null;
}

/** "us-gaap:RevenueFromContractWithCustomer" / "SellingAndMarketing" → spaced words. */
export function prettyConcept(q: string): string {
  const base = q.includes(":") ? q.split(":").pop() ?? q : q;
  return base
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1 $2")
    .replace(/_/g, " ")
    .trim();
}

/** Value in the filing's unit, compacted for reading (the raw value stays in the detail). */
export function formatValue(value?: number | null, unit?: string | null): string {
  if (value == null) return "—";
  const abs = Math.abs(value);
  const money = !unit || /^[A-Z]{3}$/.test(unit);
  let v: string;
  if (money && abs >= 1e9) v = `${(value / 1e9).toLocaleString(undefined, { maximumFractionDigits: 2 })}B`;
  else if (money && abs >= 1e6) v = `${(value / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
  else v = value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return unit ? `${v} ${unit}` : v;
}

export function factTitle(f?: FactInfo | null): string {
  if (!f) return "Financial fact";
  if (f.componentOf) {
    return `${f.label ? prettyConcept(f.label) : "Input"} (input to ${prettyConcept(f.componentOf)})`;
  }
  const q = f.citation?.qname;
  const what =
    (f.metric ? prettyConcept(f.metric) : null) ??
    (typeof q === "string" && q ? prettyConcept(q) : null) ??
    f.sectionPath ??
    "Financial fact";
  const who = [f.ticker, f.label && f.label !== f.metric ? f.label : null]
    .filter(Boolean)
    .join(" · ");
  return who ? `${what} — ${who}` : what;
}

export function factSnippet(f?: FactInfo | null): string {
  if (!f) return "";
  const parts: string[] = [];
  if (f.stepOutcome) {
    const o = f.stepOutcome;
    parts.push(`${o.op ?? "step"} ${o.status ?? ""}`.trim() + (o.error ? `: ${o.error}` : ""));
  }
  if (f.value != null) parts.push(formatValue(f.value, f.unit));
  if (f.fiscalYear != null) parts.push(`FY${f.fiscalYear}`);
  for (const [axis, member] of f.dimensions ?? []) parts.push(`${axis}: ${member}`);
  if (f.rank != null) parts.push(`rank ${f.rank}`);
  if (f.pctOfTotal != null) parts.push(`${(f.pctOfTotal * 100).toFixed(1)}% of total`);
  if (f.formula) parts.push(`= ${f.formula}`);
  return parts.join(" · ");
}

export function factMeta(f?: FactInfo | null): string {
  if (!f) return "";
  const out: string[] = [];
  if (f.sectionPath) out.push(f.sectionPath.replace(/^Statement - /, ""));
  if (f.accessionNo) out.push(`accession ${f.accessionNo}`);
  if (f.components) out.push("derived from audited inputs");
  return out.join(" · ");
}

/** EDGAR filing index for a citation (the filing that carries the fact). */
export function edgarFilingUrl(cik?: string | null, accessionNo?: string | null): string | null {
  if (!cik || !accessionNo) return null;
  const c = Number(cik);
  if (!Number.isFinite(c)) return null;
  return `https://www.sec.gov/Archives/edgar/data/${c}/${accessionNo.replace(/-/g, "")}/${accessionNo}-index.htm`;
}
