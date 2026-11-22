import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, Json } from "../../components/ui";

const LEVEL_TONE: Record<string, "red" | "amber" | "blue" | "default"> = {
  ERROR: "red", WARNING: "amber", INFO: "blue",
};

export default function LogsPage() {
  const [queryId, setQueryId] = useState("");
  const [level, setLevel] = useState("");
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (i: number) => setOpen((prev) => {
    const next = new Set(prev);
    next.has(i) ? next.delete(i) : next.add(i);
    return next;
  });
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["logs", queryId, level],
    queryFn: () => api.logs({ limit: 300, query_id: queryId || undefined, level: level || undefined }),
  });
  const items = data?.items ?? [];

  return (
    <div>
      <h1 className="page-title">Logs</h1>
      <p className="page-sub">Structured JSON log stream ({data?.path}). Filter by query_id / level.</p>

      <Card>
        <div className="flex gap-8 wrap">
          <input className="text-input mono" style={{ width: 240 }} placeholder="query_id"
            value={queryId} onChange={(e) => setQueryId(e.target.value)} />
          <select className="text-input" style={{ width: 140 }} value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">all levels</option>
            <option>INFO</option><option>WARNING</option><option>ERROR</option><option>DEBUG</option>
          </select>
          <button className="btn" onClick={() => refetch()}>Refresh</button>
        </div>
      </Card>

      {isLoading ? <p className="dim">Loading…</p> : null}
      {data?.error ? <Empty>{data.error}</Empty> : null}

      {items.length ? (
        <table>
          <thead><tr><th></th><th>Time</th><th>Level</th><th>Stage</th><th>query_id</th><th>Message</th></tr></thead>
          <tbody>
            {items.map((row, i) => {
              const r = row as Record<string, unknown>;
              const lvl = String(r.level ?? "");
              return (
                <Fragment key={i}>
                  <tr className="click" onClick={() => toggle(i)}>
                    <td className="dim">{open.has(i) ? "▾" : "▸"}</td>
                    <td className="dim mono">{String(r.ts ?? "")}</td>
                    <td><Badge tone={LEVEL_TONE[lvl] ?? "default"}>{lvl || "—"}</Badge></td>
                    <td className="mono dim">{String(r.stage ?? "")}</td>
                    <td className="mono dim">{String(r.query_id ?? "")}</td>
                    <td>{String(r.message ?? "")}</td>
                  </tr>
                  {open.has(i) ? (
                    <tr>
                      <td colSpan={6}><Json value={r} /></td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      ) : !isLoading && !data?.error ? <Empty>No matching log lines.</Empty> : null}
    </div>
  );
}
