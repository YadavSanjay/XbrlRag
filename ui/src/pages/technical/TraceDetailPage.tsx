import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import type { QueryResponse, TraceHeader } from "../../lib/types";
import { Badge, Card, Empty, Tile, ms, usd } from "../../components/ui";
import StageWaterfall from "../../components/StageWaterfall";
import EventStream from "../../components/EventStream";
import AnswerView from "../../components/AnswerView";
import { LlmCalls, ResolutionAndFallbacks, SqlQueries } from "../../components/TraceEvidence";

export default function TraceDetailPage() {
  const { queryId } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["trace", queryId],
    queryFn: () => api.trace(queryId!),
    enabled: !!queryId,
  });

  const header = data?.header;
  const events = data?.events ?? [];

  return (
    <div>
      <h1 className="page-title">Trace</h1>
      <p className="page-sub mono">{queryId}</p>

      {isLoading ? <p className="dim">Loading…</p> : null}
      {error ? <Empty>Audit store not reachable.</Empty> : null}
      {!isLoading && !header && !error ? (
        <Empty>No trace found for this query_id. It appears once the query is persisted to the audit store.</Empty>
      ) : null}

      {header ? (
        <>
          <Card>
            <div style={{ fontSize: 15, marginBottom: 10 }}>{header.query}</div>
            <div className="flex gap-8 wrap">
              {header.branch ? <Badge tone="blue">{header.branch}</Badge> : null}
              {header.intent ? <Badge tone="purple">{header.intent}</Badge> : null}
              {header.degraded ? <Badge tone="amber">degraded</Badge> : null}
              {header.error ? <Badge tone="red">error</Badge> : <Badge tone="green">ok</Badge>}
              <span className="spacer" />
              <Link to={`/tech/architecture?q=${header.query_id}`} className="badge blue">replay on map →</Link>
              <Link to={`/tech/lineage?q=${header.query_id}`} className="badge green">numeric lineage →</Link>
            </div>
          </Card>

          <div className="trace-layout">
            {/* Left column — technical detail */}
            <div>
              <div className="grid cols-4">
                <Tile label="Total latency" value={ms(header.total_latency_ms)} />
                <Tile label="Cost" value={usd(header.total_cost_usd)} />
                <Tile label="LLM calls" value={header.llm_calls ?? "—"} />
                <Tile label="Retries" value={header.total_retries ?? "—"} />
              </div>

              <Card title="Stage waterfall" hint="end-to-end request flow across pipeline stages">
                <StageWaterfall stages={header.stage_latency_ms ?? {}} />
              </Card>

              <ResolutionAndFallbacks events={events} />
              <LlmCalls events={events} />
              <SqlQueries events={events} />

              <Card
                title={`Event stream (${events.length})`}
                hint="subquery · intent_extraction · decision · llm_call · sql_query · numeric_lineage · retry · model_escalation · verifier_error"
              >
                <EventStream events={events} />
              </Card>
            </div>

            {/* Right column — the final answer, same view as the Ask page */}
            <div className="trace-answer-panel">
              <AnswerPanel header={header} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** Collapsible panel that renders the captured final answer with the full Ask-page
 *  view: Answer / Supporting statements / Not answered / How it was answered. */
function AnswerPanel({ header }: { header: TraceHeader }) {
  const [open, setOpen] = useState(true);
  // Backward-compatible: traces persisted before answer-capture have answer=null
  // (and legacy rows could carry an unexpected type) — treat anything that isn't
  // a real answer object as "no answer captured".
  const answer =
    header.answer && typeof header.answer === "object" ? header.answer : undefined;
  const hasAnswer = !!(answer && (answer.answer_summary || (answer.answer_details?.length ?? 0) > 0));
  const resp: QueryResponse = {
    query_id: header.query_id,
    mode: "xbrl",
    answer: answer ?? {},
    error: header.error ?? null,
  };

  return (
    <div>
      <div
        className="flex gap-8 items-center"
        style={{ marginBottom: 10, cursor: "pointer" }}
        onClick={() => setOpen((o) => !o)}
      >
        <h3 style={{ margin: 0 }}>Final answer</h3>
        <span className="spacer" />
        <span className="dim">{open ? "▾ collapse" : "▸ expand"}</span>
      </div>
      {open ? (
        hasAnswer ? (
          <AnswerView resp={resp} hideTraceLink />
        ) : (
          <Empty>
            No final answer was captured for this query (it predates answer capture, or the query errored before synthesis).
          </Empty>
        )
      ) : null}
    </div>
  );
}
