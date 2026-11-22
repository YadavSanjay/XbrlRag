import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, Tile, pct } from "../../components/ui";
import type { EvalFlippedCase, EvalRun } from "../../lib/types";

/**
 * WS-9 / E4 — regression diff between two runs.
 *
 * Regressions are listed first: what used to work and no longer does is the thing
 * you act on. A cross-corpus comparison is refused rather than rendered — two runs
 * measured on different corpus states are not commensurable, and a delta between
 * them would read as a quality change when it is a data change.
 */

function MetricRow({
  label,
  a,
  b,
  invert = false,
}: {
  label: string;
  a?: number | null;
  b?: number | null;
  invert?: boolean;
}) {
  const delta = a != null && b != null ? b - a : null;
  const good = delta == null ? null : invert ? delta < 0 : delta > 0;
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{pct(a)}</td>
      <td className="num">{pct(b)}</td>
      <td className="num">
        {delta == null ? (
          <span className="muted">—</span>
        ) : Math.abs(delta) < 0.005 ? (
          <span className="muted">no change</span>
        ) : (
          <Badge tone={good ? "green" : "red"}>
            {(delta > 0 ? "+" : "") + (delta * 100).toFixed(1)} pts
          </Badge>
        )}
      </td>
    </tr>
  );
}

function FlipTable({ rows, title, tone }: { rows: EvalFlippedCase[]; title: string; tone: "red" | "green" }) {
  if (!rows.length) return null;
  return (
    <Card title={`${title} (${rows.length})`}>
      <table className="table compact">
        <thead>
          <tr>
            <th>Case</th>
            <th>Question</th>
            <th>A → B</th>
            <th>Reason in B</th>
            <th>Traces</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.case_id}>
              <td>
                <code className="small">{r.case_id}</code>
              </td>
              <td>{r.question}</td>
              <td>
                <Badge tone={tone === "red" ? "green" : "red"}>{r.grade_a}</Badge> →{" "}
                <Badge tone={tone === "red" ? "red" : "green"}>{r.grade_b}</Badge>
              </td>
              <td className="small muted">{r.reason_b}</td>
              <td className="small">
                {r.query_id_a && <Link to={`/tech/traces/${r.query_id_a}`}>A</Link>}
                {r.query_id_a && r.query_id_b ? " · " : ""}
                {r.query_id_b && <Link to={`/tech/traces/${r.query_id_b}`}>B</Link>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function RunHeader({ run, label }: { run: EvalRun | null; label: string }) {
  if (!run) return <Empty>{label} not found.</Empty>;
  return (
    <div>
      <strong>{label}</strong> <code>{run.run_id}</code>
      <div className="muted small">
        {run.suite}_{run.suite_version} · {run.system} · {new Date(run.created_at).toLocaleString()} ·{" "}
        {run.n_cases} cases
      </div>
    </div>
  );
}

export default function EvalComparePage() {
  const { a = "", b = "" } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["evalCompare", a, b],
    queryFn: () => api.evalCompare(a, b),
  });

  if (isLoading) return <p className="muted">Loading comparison…</p>;
  if (error) return <Empty>Could not load comparison: {String(error)}</Empty>;
  if (!data?.run_a || !data?.run_b) return <Empty>One or both runs were not found.</Empty>;

  const { run_a: A, run_b: B } = data;

  return (
    <div>
      <h1 className="page-title">Run comparison</h1>
      <div className="grid-2">
        <RunHeader run={A} label="A" />
        <RunHeader run={B} label="B" />
      </div>

      {data.corpus_mismatch && (
        <Card title="⚠ Different corpus states">
          <p>
            A was measured on <code>{A.corpus_fingerprint}</code> and B on{" "}
            <code>{B.corpus_fingerprint}</code>. These numbers are <strong>not comparable</strong> —
            a difference here may be filings loaded or re-gated between the runs, not a change in
            answer quality. Treat the deltas below as indicative only.
          </p>
        </Card>
      )}

      <Card title="Metrics">
        <table className="table compact">
          <thead>
            <tr>
              <th>Metric</th>
              <th className="num">A</th>
              <th className="num">B</th>
              <th className="num">Δ</th>
            </tr>
          </thead>
          <tbody>
            <MetricRow label="Accuracy" a={A.accuracy} b={B.accuracy} />
            <MetricRow label="Citation rate" a={A.citation_rate} b={B.citation_rate} />
            <MetricRow label="Grounding precision" a={A.grounding_precision} b={B.grounding_precision} />
            <MetricRow label="Silent-wrong rate" a={A.silent_wrong_rate} b={B.silent_wrong_rate} invert />
            <MetricRow label="Refusal rate" a={A.refusal_rate} b={B.refusal_rate} invert />
            <MetricRow label="Error rate" a={A.error_rate} b={B.error_rate} invert />
          </tbody>
        </table>
        <div className="tiles">
          <Tile label="Regressions" value={data.regressions.length} />
          <Tile label="Improvements" value={data.improvements.length} />
        </div>
      </Card>

      <FlipTable rows={data.regressions} title="Regressions — passed in A, failing in B" tone="red" />
      <FlipTable rows={data.improvements} title="Improvements — failing in A, passing in B" tone="green" />

      {!data.regressions.length && !data.improvements.length && (
        <Empty>No cases changed verdict between these two runs.</Empty>
      )}
    </div>
  );
}
