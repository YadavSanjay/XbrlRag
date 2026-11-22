import type {
  AnswerPayload,
  ExecutionResult,
  FaithfulnessReport,
  PlanStep,
  StepResult,
  SubQueryMeta,
} from "./types";

/**
 * Pure normalization of an answer payload into the parts it was answered in.
 *
 * A simple query is one part, read from the top-level `plan` / `execution_result`.
 * A compound query is split by the QueryDecomposer and answered sub-query by
 * sub-query; `XbrlRetrievalPipeline._merge_responses` then nests each sub-query's
 * plan, execution result and faithfulness report under `metadata.subqueries[]`
 * (and leaves `plan.decomposed` / `execution_result.subqueries`). Every consumer
 * that walks steps — the stepper, the citation registry — goes through `answerParts`
 * so it sees the same steps either way.
 */

export interface AnswerPart {
  /** Sub-query id (`sq1`, …) for a decomposed answer; null for a simple one. */
  id: string | null;
  query: string | null;
  dependsOn: string[];
  fullyAnswered: boolean | null;
  planSteps: PlanStep[];
  execution: ExecutionResult | null;
  steps: StepResult[];
  faithfulness: FaithfulnessReport | null;
}

function asBool(v: unknown): boolean | null {
  if (v === true || v === "true") return true;
  if (v === false || v === "false") return false;
  return null;
}

function asObject<T>(v: unknown): T | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as T) : null;
}

export function isDecomposed(a: AnswerPayload): boolean {
  return asBool(a.metadata?.decomposed) === true;
}

export function answerParts(a: AnswerPayload): AnswerPart[] {
  const meta = a.metadata ?? {};
  if (isDecomposed(a) && Array.isArray(meta.subqueries)) {
    return (meta.subqueries as SubQueryMeta[]).map((sq) => {
      const exec = asObject<ExecutionResult>(sq.execution_result);
      return {
        id: sq.id ?? null,
        query: sq.query ?? null,
        dependsOn: sq.depends_on ?? [],
        fullyAnswered: asBool(sq.fully_answered),
        planSteps: asObject<{ steps?: PlanStep[] }>(sq.plan)?.steps ?? [],
        execution: exec,
        steps: exec?.steps ?? [],
        faithfulness: asObject<FaithfulnessReport>(sq.faithfulness),
      };
    });
  }
  const exec = asObject<ExecutionResult>(a.execution_result);
  return [
    {
      id: null,
      query: a.query ?? null,
      dependsOn: [],
      fullyAnswered: asBool(meta.fully_answered),
      planSteps: a.plan?.steps ?? [],
      execution: exec,
      steps: exec?.steps ?? [],
      faithfulness: asObject<FaithfulnessReport>(meta.faithfulness),
    },
  ];
}

export interface SubAnswer {
  id: string | null;
  question: string | null;
  answer: string;
}

/**
 * Split a decomposed answer's summary back into its "[sqN] question\nanswer" blocks
 * (the format `_merge_responses` joins them in). Text that does not match the format
 * comes back as one untitled block, so nothing is ever hidden.
 */
export function splitSubAnswers(summary: string): SubAnswer[] {
  const blocks = summary.split(/\n\n(?=\[[^\]\n]+\] )/);
  return blocks
    .filter((b) => b.trim())
    .map((b) => {
      const m = /^\[([^\]\n]+)\] ([^\n]*)\n([\s\S]*)$/.exec(b);
      return m ? { id: m[1], question: m[2], answer: m[3].trim() } : { id: null, question: null, answer: b };
    });
}

/** The plan step a result came from (results carry `step_id`, plans carry `id`). */
export function planStepFor(part: AnswerPart, stepId?: string | null): PlanStep | undefined {
  if (!stepId) return undefined;
  return part.planSteps.find((p) => p.id === stepId);
}

/** The metric a plan step asks for, in the words an analyst would read. */
export function stepMetric(p?: PlanStep): string | null {
  if (!p) return null;
  if (typeof p.concept_family === "string" && p.concept_family) return p.concept_family;
  if (typeof p.metric_kind === "string" && p.metric_kind.startsWith("derived:")) {
    return p.metric_kind.slice("derived:".length);
  }
  if (typeof p.concept === "string" && p.concept) return p.concept;
  return null;
}

/** Plan-step dimension hints as "axis: member" pairs (the planner sometimes emits "NONE"). */
export function stepDimensions(p?: PlanStep | null, fallback?: unknown): Array<[string, string]> {
  const raw = asObject<Record<string, unknown>>(p?.dimensions) ?? asObject<Record<string, unknown>>(fallback);
  if (!raw) return [];
  return Object.entries(raw)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => [k, String(v)]);
}

/**
 * Verifier verdicts other than SUPPORTED. UNSUPPORTED statements were dropped from
 * `answer_details`; PARTIALLY_SUPPORTED ones were kept as-is
 * (llm_xbrl_faithfulness_verifier.py).
 */
export function flaggedStatements(parts: AnswerPart[]) {
  return parts.flatMap((p) =>
    (p.faithfulness?.verdicts ?? [])
      .filter((v) => v.verdict && v.verdict !== "SUPPORTED")
      .map((v) => ({ part: p.id, dropped: v.verdict === "UNSUPPORTED", ...v })),
  );
}
