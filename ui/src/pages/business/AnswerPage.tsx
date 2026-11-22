import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { loadAnswer } from "../../lib/answerStore";
import type { QueryResponse } from "../../lib/types";
import AnswerView from "../../components/AnswerView";
import { Empty } from "../../components/ui";

export default function AnswerPage() {
  const { queryId } = useParams();
  const cached = queryId ? loadAnswer(queryId) : null;

  // Deep-link / refresh fallback: the full answer is persisted with the trace
  // (audit.query_audit.answer), so fetch it when it isn't in this session's cache.
  const trace = useQuery({
    queryKey: ["trace-answer", queryId],
    queryFn: () => api.trace(queryId!),
    enabled: !!queryId && !cached,
  });

  const fromTrace: QueryResponse | null = trace.data?.header?.answer
    ? {
        query_id: queryId ?? null,
        mode: "xbrl",
        answer: trace.data.header.answer,
        error: trace.data.header.error ?? null,
      }
    : null;

  const resp = cached ?? fromTrace;

  return (
    <div>
      <h1 className="page-title">Answer</h1>
      <p className="page-sub mono">{queryId}</p>
      {resp ? (
        <AnswerView resp={resp} />
      ) : trace.isFetching ? (
        <p className="dim">Loading answer…</p>
      ) : (
        <Empty>
          This answer isn't available. <Link to="/business/ask">Ask a new question →</Link>
          {queryId ? <> or <Link to={`/tech/traces/${queryId}`}>open its trace →</Link></> : null}
        </Empty>
      )}
    </div>
  );
}
