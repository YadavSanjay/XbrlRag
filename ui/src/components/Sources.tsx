import { useMemo, type ReactNode } from "react";
import type { AnswerPayload } from "../lib/types";
import { Card, KV, SlideOver } from "./ui";
import {
  buildAnswerSources,
  edgarFilingUrl,
  factTitle,
  factSnippet,
  factMeta,
  formatValue,
  prettyConcept,
  type AnswerSource,
  type AnswerSourcesResult,
  type FactInfo,
} from "./sourcesRegistry";

/**
 * Analyst-facing citation layer for the Ask/Answer view. We never show raw
 * fact/chunk ids on the business surface (they belong in the technical trace);
 * a `CitationChip` carries a source number and opens the readable source,
 * `SourcesCard` lists them, and `SourceDetail` (a SlideOver) reveals the full
 * citation — the one place raw ids live on the business side.
 *
 * The join itself is pure, in `sourcesRegistry.ts`.
 */

export type { AnswerSource } from "./sourcesRegistry";

export function useAnswerSources(a: AnswerPayload): AnswerSourcesResult {
  return useMemo(() => buildAnswerSources(a), [a]);
}

/** A small numbered, tappable reference tying a claim to a source. */
export function CitationChip({
  src,
  onOpen,
}: {
  src: AnswerSource;
  onOpen: (s: AnswerSource) => void;
}) {
  const label = src.kind === "fact" ? factTitle(src.fact) : "Filing excerpt";
  return (
    <button
      type="button"
      className="cite"
      onClick={() => onOpen(src)}
      title={`Source ${src.n}: ${label}`}
      aria-label={`Open source ${src.n}, ${label}`}
    >
      {src.n}
    </button>
  );
}

/** Readable evidence list — one card per numbered source. */
export function SourcesCard({
  sources,
  onOpen,
}: {
  sources: AnswerSource[];
  onOpen: (s: AnswerSource) => void;
}) {
  if (!sources.length) return null;
  return (
    <Card title="Sources" hint="the facts and filing excerpts this answer is drawn from">
      {sources.map((s) => (
        <SourceRow key={`${s.kind}:${s.id}`} src={s} onOpen={onOpen} />
      ))}
    </Card>
  );
}

function SourceRow({ src, onOpen }: { src: AnswerSource; onOpen: (s: AnswerSource) => void }) {
  const isFact = src.kind === "fact";
  const title = isFact ? factTitle(src.fact) : "Filing excerpt";
  const snippet = isFact ? factSnippet(src.fact) : src.text ?? "";
  const meta = isFact
    ? factMeta(src.fact)
    : src.text
    ? "SEC filing narrative"
    : "SEC filing narrative (excerpt not captured)";
  return (
    <div className="src">
      <span className="src-idx">{src.n}</span>
      <div className="src-body">
        <div className="src-title">{title}</div>
        {snippet ? <div className="src-snip">{snippet}</div> : null}
        {meta ? <div className="src-meta">{meta}</div> : null}
      </div>
      <button type="button" className="src-open" onClick={() => onOpen(src)}>
        Open source →
      </button>
    </div>
  );
}

/** Full source detail in the shared drill-down panel. Raw ids live here — the
 *  one place they belong on the business side. */
export function SourceDetail({ src, onClose }: { src: AnswerSource; onClose: () => void }) {
  return (
    <SlideOver title={`Source ${src.n}`} onClose={onClose}>
      {src.kind === "fact" ? (
        <FactDetail id={src.id} fact={src.fact} />
      ) : (
        <>
          <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {src.text ?? "(no excerpt text was captured for this chunk)"}
          </p>
          <div className="dim mono mt-8" style={{ fontSize: 11 }}>
            chunk id: {src.id}
          </div>
        </>
      )}
    </SlideOver>
  );
}

function FactDetail({ id, fact }: { id: string; fact?: FactInfo | null }) {
  if (!fact) {
    return (
      <p className="dim mb-0">
        Fact <span className="mono">{id}</span> is cited, but no executed step in this answer
        carries it. Open the technical trace to see the lookup that produced it.
      </p>
    );
  }
  const rows: Array<[string, ReactNode]> = [];
  const add = (k: string, v: ReactNode) => {
    if (v != null && v !== "") rows.push([k, v]);
  };
  if (fact.stepOutcome) {
    add("Plan step", <span className="mono">{fact.factId}</span>);
    add("Outcome", `${fact.stepOutcome.op ?? "step"} ${fact.stepOutcome.status ?? ""}`);
    add("Reason", fact.stepOutcome.error);
  }
  add("Metric", fact.metric ? prettyConcept(fact.metric) : null);
  if (fact.componentOf) add("Input to", prettyConcept(fact.componentOf));
  add(fact.rowOf === "breakdown" ? "Member" : "Label", fact.label);
  add("Company", fact.ticker);
  add("Fiscal year", fact.fiscalYear != null ? `FY${fact.fiscalYear}` : null);
  for (const [axis, member] of fact.dimensions ?? []) add(`Dimension (${axis})`, member);
  if (fact.value != null) {
    add(
      "Value",
      <span>
        {formatValue(fact.value, fact.unit)}{" "}
        <span className="dim mono">({fact.value.toLocaleString()})</span>
      </span>,
    );
  }
  add("Rank", fact.rank);
  add("Share of total", fact.pctOfTotal != null ? `${(fact.pctOfTotal * 100).toFixed(1)}%` : null);
  add("Formula", fact.formula ? <code className="mono">{fact.formula}</code> : null);
  if (fact.components) {
    add(
      "Audited inputs",
      <span className="mono">
        {Object.entries(fact.components)
          .map(([k, v]) => `${k}: fact ${String(v)}`)
          .join(" · ")}
      </span>,
    );
  }
  add("Section", fact.sectionPath);
  const filing = edgarFilingUrl(fact.cik, fact.accessionNo);
  add(
    "Filing",
    fact.accessionNo ? (
      filing ? (
        <a href={filing} target="_blank" rel="noreferrer noopener">
          {fact.accessionNo} ↗
        </a>
      ) : (
        fact.accessionNo
      )
    ) : null,
  );
  add("Anchor", fact.htmlAnchor ? <span className="mono">{fact.htmlAnchor}</span> : null);
  add("Fact id", <span className="mono">{fact.factId}</span>);
  return (
    <>
      <KV rows={rows} />
      {fact.factId.includes(":") ? (
        <p className="dim mt-8 mb-0" style={{ fontSize: 12 }}>
          A derived metric: no single tagged fact carries this value; it is computed from the
          audited inputs listed above.
        </p>
      ) : null}
    </>
  );
}
