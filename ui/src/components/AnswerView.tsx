import { useState } from "react";
import { Link } from "react-router-dom";
import type { QueryResponse } from "../lib/types";
import { downloadMarkdown } from "../lib/exportAnswer";
import { Badge, Card } from "./ui";
import StageStepper from "./StageStepper";
import { isDecomposed, splitSubAnswers } from "../lib/answerModel";
import {
  useAnswerSources,
  CitationChip,
  SourcesCard,
  SourceDetail,
  type AnswerSource,
} from "./Sources";

export default function AnswerView({ resp, hideTraceLink }: { resp: QueryResponse; hideTraceLink?: boolean }) {
  const a = resp.answer ?? {};
  const meta = a.metadata ?? {};
  const fully = meta.fully_answered;
  const details = a.answer_details ?? [];
  const unanswered = a.unanswered_parts ?? [];
  const { sources, perStatement } = useAnswerSources(a);
  const [openSource, setOpenSource] = useState<AnswerSource | null>(null);
  const subAnswers = isDecomposed(a) ? splitSubAnswers(a.answer_summary ?? "") : null;

  if (resp.error) {
    return (
      <Card title="Query error">
        <p className="mb-0">{resp.error}</p>
      </Card>
    );
  }

  return (
    <div>
      {/* Confidence banner */}
      <div className="flex gap-8 items-center mb-12">
        <Badge tone={fully ? "green" : "amber"}>{fully ? "Fully answered" : "Partial / review"}</Badge>
        {meta.degraded ? <Badge tone="amber">degraded fallback</Badge> : null}
        <span className="spacer" />
        <button className="btn ghost sm no-print" onClick={() => downloadMarkdown(resp)}>⬇ Export .md</button>
        <button className="btn ghost sm no-print" onClick={() => window.print()}>🖨 Print / PDF</button>
        {hideTraceLink ? null : resp.query_id ? (
          <Link to={`/tech/traces/${resp.query_id}`} className="badge blue">
            View full technical trace →
          </Link>
        ) : (
          <span className="dim">no trace id (sec10k path)</span>
        )}
      </div>

      {/* Headline answer. A compound question was split by the decomposer and
          answered per sub-query; its summary is one "[sqN] question\nanswer" block
          per part (retrieval_pipeline._merge_responses). */}
      <Card title="Answer" hint={subAnswers ? `compound question — answered in ${subAnswers.length} parts` : undefined}>
        {subAnswers ? (
          subAnswers.map((b, i) => (
            <div key={i} className={i ? "mt-8" : undefined}>
              <div className="flex gap-8 items-center" style={{ marginBottom: 4 }}>
                {b.id ? <Badge tone="purple">{b.id}</Badge> : null}
                <span className="dim">{b.question}</span>
              </div>
              <p className="answer-text" style={{ whiteSpace: "pre-line" }}>{b.answer}</p>
            </div>
          ))
        ) : (
          <p className="answer-text" style={{ whiteSpace: "pre-line" }}>
            {a.answer_summary || "No answer was produced. Try rephrasing the question, or switch retrieval mode above."}
          </p>
        )}
      </Card>

      {/* Supported statements — each claim carries numbered citation chips that
          open the readable source. Raw fact/chunk ids stay off this surface. */}
      {details.length ? (
        <Card title="Supporting statements" hint="each claim links to the sources that back it">
          {details.map((d, i) => (
            <div key={i} className="stmt">
              <span>{d.statement}</span>
              {perStatement[i]?.map((n) => {
                const src = sources.find((s) => s.n === n);
                return src ? <CitationChip key={n} src={src} onOpen={setOpenSource} /> : null;
              })}
            </div>
          ))}
        </Card>
      ) : null}

      {/* Readable evidence — the sources behind the answer, one glance away. */}
      <SourcesCard sources={sources} onOpen={setOpenSource} />

      {/* Unanswered parts */}
      {unanswered.length ? (
        <Card title="Not answered">
          {unanswered.map((u, i) => (
            <div key={i} className="mt-8">
              <strong>{u.question_fragment}</strong>
              <div className="dim">{u.reason}</div>
            </div>
          ))}
        </Card>
      ) : null}

      {/* Explainability — the glass box (A3) */}
      <Card title="How it was answered" hint="each stage maps to a real pipeline stage; click to expand">
        <StageStepper answer={a} />
      </Card>

      {openSource ? <SourceDetail src={openSource} onClose={() => setOpenSource(null)} /> : null}
    </div>
  );
}
