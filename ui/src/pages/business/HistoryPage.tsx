import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { Badge, Empty, ms, usd } from "../../components/ui";
import { matches } from "../../lib/tableControls";

export default function HistoryPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["traces", 100],
    queryFn: () => api.traces(100),
  });
  const all = data?.items ?? [];
  const [sel, setSel] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const navigate = useNavigate();

  function toggle(id: string) {
    setSel((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-2));
  }

  const statusOf = (t: (typeof all)[number]) => (t.error ? "error" : t.fully_answered ? "answered" : "partial");
  const items = useMemo(
    () => all.filter((t) => matches([t.query, t.branch, t.intent], q) && (!status || statusOf(t) === status)),
    [all, q, status],
  );

  return (
    <div>
      <h1 className="page-title">History</h1>
      <p className="page-sub">Past queries (from the audit store). Open one, or pick two to compare.</p>

      <div className="filterbar">
        <input className="grow" placeholder="Search past queries…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">any status</option>
          <option value="answered">answered</option>
          <option value="partial">partial</option>
          <option value="error">error</option>
        </select>
        <span className="dim">{sel.length}/2 selected</span>
        <button className="btn" disabled={sel.length !== 2}
          onClick={() => navigate(`/business/compare?a=${sel[0]}&b=${sel[1]}`)}>
          Compare selected →
        </button>
      </div>

      {isLoading ? <p className="dim">Loading…</p> : null}
      {error ? <Empty>Audit store not reachable — history appears once the BFF can read Postgres.</Empty> : null}

      {items.length ? (
        <table>
          <thead>
            <tr><th></th><th>Query</th><th>Branch</th><th>Intent</th><th>Status</th><th>Latency</th><th>Cost</th><th>When</th></tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.query_id} className="click" onClick={() => navigate(`/tech/traces/${t.query_id}`)}>
                <td onClick={(e) => { e.stopPropagation(); toggle(t.query_id); }}>
                  <input type="checkbox" checked={sel.includes(t.query_id)} readOnly />
                </td>
                <td>{t.query?.slice(0, 56)}</td>
                <td>{t.branch ?? "—"}</td>
                <td>{t.intent ?? "—"}</td>
                <td>
                  {t.error ? <Badge tone="red">error</Badge>
                    : t.fully_answered ? <Badge tone="green">answered</Badge>
                    : <Badge tone="amber">partial</Badge>}
                </td>
                <td className="mono">{ms(t.total_latency_ms)}</td>
                <td className="mono">{usd(t.total_cost_usd)}</td>
                <td className="dim">{t.started_at ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : !isLoading && !error ? (
        <Empty>No queries recorded yet. <Link to="/business/ask">Ask one →</Link></Empty>
      ) : null}
    </div>
  );
}
