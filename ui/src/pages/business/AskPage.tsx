import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { saveAnswer } from "../../lib/answerStore";
import type { QueryMode, QueryResponse, StreamEvent } from "../../lib/types";
import AnswerView from "../../components/AnswerView";
import LiveProgress from "../../components/LiveProgress";
import { Card } from "../../components/ui";

const EXAMPLES = [
  "What was NVIDIA's Data Center revenue in FY2026?",
  "What drove the growth in NVIDIA's Data Center revenue in FY2026?",
  "What are the main risk factors NVIDIA discusses related to export controls?",
];

const MODES: { value: QueryMode; label: string }[] = [
  { value: "auto", label: "Auto (XBRL hybrid)" },
  { value: "xbrl", label: "XBRL (numeric + narrative)" },
  { value: "basic", label: "Basic (10-K RAG)" },
  { value: "hyde", label: "HyDE" },
  { value: "subquery", label: "Sub-query" },
];

export default function AskPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<QueryMode>("auto");
  const [streaming, setStreaming] = useState(false);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [resp, setResp] = useState<QueryResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const navigate = useNavigate();

  // Close the stream if the user navigates away mid-query.
  useEffect(() => () => esRef.current?.close(), []);

  function submit() {
    const q = query.trim();
    if (!q || streaming) return;
    esRef.current?.close();
    setErr(null);
    setResp(null);
    setEvents([]);
    setStreaming(true);

    const es = new EventSource(api.streamUrl({ query: q, mode }));
    esRef.current = es;

    es.onmessage = (msg) => {
      let ev: StreamEvent;
      try {
        ev = JSON.parse(msg.data) as StreamEvent;
      } catch {
        return;
      }
      if (ev.kind === "result") {
        const r: QueryResponse = {
          query_id: ev.query_id ?? null,
          mode: ev.mode ?? mode,
          answer: ev.answer ?? {},
          error: ev.error ?? null,
        };
        setResp(r);
        if (r.error) setErr(r.error);
        const id = saveAnswer(r);
        setStreaming(false);
        es.close();
        if (r.query_id) navigate(`/business/answer/${r.query_id}`);
        else void id;
        return;
      }
      setEvents((prev) => [...prev, ev]);
    };

    es.onerror = () => {
      // EventSource auto-reconnects; for a one-shot query we stop on error.
      es.close();
      setStreaming(false);
      setErr((e) => e ?? "The connection dropped before an answer came back. Check that the API is running, then ask again.");
    };
  }

  return (
    <div>
      <h1 className="page-title">Ask</h1>
      <p className="page-sub">Ask a fundamental question about a company's SEC filings.</p>

      <Card>
        <textarea
          rows={3}
          placeholder="e.g. What was NVIDIA's Data Center revenue in FY2026?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <div className="flex gap-12 items-center mt-8 wrap">
          <select className="text-input" style={{ width: 240 }} value={mode}
            onChange={(e) => setMode(e.target.value as QueryMode)}>
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <button className="btn" disabled={streaming || !query.trim()} onClick={submit}>
            {streaming ? "Running…" : "Ask  (⌘/Ctrl+↵)"}
          </button>
          <span className="spacer" />
          <span className="dim">streams live pipeline progress</span>
        </div>
        <div className="flex gap-8 wrap mt-8">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="btn ghost sm" onClick={() => setQuery(ex)}>{ex}</button>
          ))}
        </div>
      </Card>

      {err ? <Card title="Error"><p className="mb-0">{err}</p></Card> : null}

      {(streaming || events.length > 0) && !resp ? (
        <LiveProgress events={events} done={!streaming} />
      ) : null}

      {resp ? <AnswerView resp={resp} /> : null}
    </div>
  );
}
