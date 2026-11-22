import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Empty, SortTh, ms, usd } from "../../components/ui";
import { matches, sortRows, type SortDir } from "../../lib/tableControls";

const PAGE = 50;

export default function TracesPage() {
  const [sp] = useSearchParams();
  const { data, isLoading, error } = useQuery({ queryKey: ["traces", 200], queryFn: () => api.traces(200) });
  const all = data?.items ?? [];
  const navigate = useNavigate();

  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [branch, setBranch] = useState(sp.get("branch") ?? "");
  const [sortKey, setSortKey] = useState("started_at");
  const [dir, setDir] = useState<SortDir>("desc");
  const [limit, setLimit] = useState(PAGE);

  const branches = useMemo(() => [...new Set(all.map((t) => t.branch).filter(Boolean))] as string[], [all]);

  const statusOf = (t: (typeof all)[number]) => (t.error ? "error" : t.degraded ? "degraded" : "ok");

  const view = useMemo(() => {
    let rows = all.filter((t) =>
      matches([t.query_id, t.query, t.branch, t.intent], q) &&
      (!status || statusOf(t) === status) &&
      (!branch || t.branch === branch),
    );
    const acc: Record<string, (t: (typeof all)[number]) => number | string | undefined> = {
      started_at: (t) => t.started_at,
      total_latency_ms: (t) => t.total_latency_ms,
      total_cost_usd: (t) => t.total_cost_usd,
      llm_calls: (t) => t.llm_calls,
      query: (t) => t.query,
    };
    rows = sortRows(rows, acc[sortKey] ?? acc.started_at, dir);
    return rows;
  }, [all, q, status, branch, sortKey, dir]);

  function onSort(col: string) {
    if (col === sortKey) setDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(col); setDir("desc"); }
  }

  return (
    <div>
      <h1 className="page-title">Trace Explorer</h1>
      <p className="page-sub">Recent queries. Filter, sort, and select one to see the end-to-end request flow.</p>

      <div className="filterbar">
        <input className="grow" placeholder="Search query / id / intent…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">any status</option>
          <option value="ok">ok</option>
          <option value="degraded">degraded</option>
          <option value="error">error</option>
        </select>
        <select value={branch} onChange={(e) => setBranch(e.target.value)}>
          <option value="">any branch</option>
          {branches.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <span className="dim">{view.length} of {all.length}</span>
      </div>

      {isLoading ? <p className="dim">Loading…</p> : null}
      {error ? <Empty>Audit store not reachable.</Empty> : null}

      {view.length ? (
        <>
          <table>
            <thead>
              <tr>
                <th>query_id</th>
                <SortTh label="Query" col="query" active={sortKey} dir={dir} onSort={onSort} />
                <th>Branch</th><th>Intent</th><th>Status</th>
                <SortTh label="Latency" col="total_latency_ms" active={sortKey} dir={dir} onSort={onSort} />
                <SortTh label="Cost" col="total_cost_usd" active={sortKey} dir={dir} onSort={onSort} />
                <SortTh label="LLM" col="llm_calls" active={sortKey} dir={dir} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {view.slice(0, limit).map((t) => (
                <tr key={t.query_id} className="click" onClick={() => navigate(`/tech/traces/${t.query_id}`)}>
                  <td className="mono">{t.query_id?.slice(0, 10)}</td>
                  <td>{t.query?.slice(0, 48)}</td>
                  <td>{t.branch ?? "—"}</td>
                  <td>{t.intent ?? "—"}</td>
                  <td>
                    {t.error ? <Badge tone="red">error</Badge>
                      : t.degraded ? <Badge tone="amber">degraded</Badge>
                      : <Badge tone="green">ok</Badge>}
                  </td>
                  <td className="mono">{ms(t.total_latency_ms)}</td>
                  <td className="mono">{usd(t.total_cost_usd)}</td>
                  <td className="mono">{t.llm_calls ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {view.length > limit ? (
            <div className="flex gap-8" style={{ marginTop: 12 }}>
              <button className="btn ghost" onClick={() => setLimit((l) => l + PAGE)}>Show more ({view.length - limit} left)</button>
            </div>
          ) : null}
        </>
      ) : !isLoading && !error ? <Empty>No matching traces.</Empty> : null}
    </div>
  );
}
