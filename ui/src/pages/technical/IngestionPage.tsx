import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, KV, SlideOver, Tile, ms, statusTone } from "../../components/ui";

const s = (v: unknown) => String(v ?? "");
const n = (v: unknown) => Number(v) || 0;

export default function IngestionPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["ingestion"], queryFn: api.ingestionRuns });
  const analytics = useQuery({ queryKey: ["ingestionAnalytics"], queryFn: api.ingestionAnalytics });
  const [runId, setRunId] = useState<string | null>(null);

  const items = data?.items ?? [];
  const failures = analytics.data?.failures ?? [];
  const retries = (analytics.data?.retries ?? [])[0] as Record<string, unknown> | undefined;

  const retriedOk = n(retries?.retried_then_succeeded);
  const retriedFail = n(retries?.retried_still_failed);

  return (
    <div>
      <h1 className="page-title">Ingestion & Data Ops</h1>
      <p className="page-sub">Run history with per-filing outcomes, failure triage and retry effectiveness.</p>

      {(failures.length || retries) && (
        <div className="grid cols-2">
          <Card title="Failure triage" hint="audit.v_ingestion_failures — retryable vs permanent">
            {failures.length ? (
              <table>
                <thead><tr><th>Failure class</th><th>Retryability</th><th>Failures</th><th>Runs affected</th></tr></thead>
                <tbody>
                  {failures.map((f, i) => {
                    const r = f as Record<string, unknown>;
                    return (
                      <tr key={i}>
                        <td className="mono">{s(r.failure_class) || "—"}</td>
                        <td><Badge tone={s(r.retryability) === "retryable" ? "amber" : "red"}>{s(r.retryability)}</Badge></td>
                        <td className="mono">{s(r.failures)}</td>
                        <td className="mono dim">{s(r.runs_affected)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <Empty>No failures recorded.</Empty>}
          </Card>

          <Card title="Retry effectiveness" hint="audit.v_ingestion_retries — did re-running help?">
            {retries ? (
              <>
                <div className="grid cols-2">
                  <Tile label="Retried → succeeded" value={retriedOk} />
                  <Tile label="Retried → still failed" value={retriedFail} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <KV rows={[
                    ["Items retried", <span className="mono">{s(retries.items_retried)}</span>],
                    ["Recovery rate", <span className="mono">{retriedOk + retriedFail > 0 ? `${Math.round((retriedOk / (retriedOk + retriedFail)) * 100)}%` : "—"}</span>],
                    ["Max attempts", <span className="mono">{s(retries.max_attempts)}</span>],
                  ]} />
                </div>
              </>
            ) : <Empty>No retry data.</Empty>}
          </Card>
        </div>
      )}

      <Card title="Runs" hint="click a run to see per-filing outcomes">
        {isLoading ? <p className="dim">Loading…</p> : null}
        {error ? <Empty>Audit store not reachable.</Empty> : null}
        {items.length ? (
          <table>
            <thead>
              <tr><th>Pipeline</th><th>Scope</th><th>Status</th><th>Items (ok/skip/fail)</th><th>Duration</th><th>Host</th><th>When</th></tr>
            </thead>
            <tbody>
              {items.map((row, i) => {
                const r = row as Record<string, unknown>;
                return (
                  <tr key={i} className="click" onClick={() => setRunId(s(r.run_id))}>
                    <td className="mono">{s(r.pipeline)}{r.dry_run ? " (dry)" : ""}</td>
                    <td>{s(r.scope) || "—"}</td>
                    <td><Badge tone={statusTone(s(r.status))}>{s(r.status) || "?"}</Badge></td>
                    <td className="mono">
                      {s(r.items_succeeded)}/{s(r.items_skipped)}/
                      <span style={{ color: n(r.items_failed) ? "var(--red)" : undefined }}>{s(r.items_failed)}</span>
                    </td>
                    <td className="mono">{ms(r.duration_ms as number)}</td>
                    <td className="dim">{s(r.host)}</td>
                    <td className="dim">{s(r.started_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : !isLoading && !error ? <Empty>No ingestion runs recorded.</Empty> : null}
      </Card>

      {runId && <RunItems runId={runId} onClose={() => setRunId(null)} />}
    </div>
  );
}

function RunItems({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["ingestionItems", runId],
    queryFn: () => api.ingestionRunItems(runId),
  });
  const items = data?.items ?? [];
  return (
    <SlideOver title={<span>Run · <span className="mono">{runId.slice(0, 12)}</span></span>} onClose={onClose}>
      {isLoading ? <Empty>Loading…</Empty> : null}
      {items.length ? (
        <table>
          <thead><tr><th>#</th><th>Ticker</th><th>FY</th><th>Form</th><th>Status</th><th>Failure</th><th>Att.</th></tr></thead>
          <tbody>
            {items.map((row, i) => {
              const r = row as Record<string, unknown>;
              const failed = ["failed", "error"].includes(s(r.status).toLowerCase());
              return (
                <tr key={i} title={s(r.error)}>
                  <td className="mono dim">{s(r.seq)}</td>
                  <td className="mono">{s(r.ticker) || "—"}</td>
                  <td className="mono dim">{s(r.fiscal_year)}</td>
                  <td className="dim">{s(r.form_type)}</td>
                  <td><Badge tone={statusTone(s(r.status))}>{s(r.status)}</Badge></td>
                  <td className="mono" style={failed ? { color: "var(--red)" } : undefined}>{s(r.failure_class) || "—"}</td>
                  <td className="mono dim">{s(r.attempts)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : !isLoading ? <Empty>No per-filing items recorded for this run.</Empty> : null}
    </SlideOver>
  );
}
