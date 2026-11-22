import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, Tile, ms, pct, usd } from "../../components/ui";
import type { EvalRun } from "../../lib/types";

/**
 * WS-9 / E4 — evaluation run history.
 *
 * The headline is deliberately NOT accuracy alone. Silent-wrong rate (confident +
 * cited + wrong) is the release gate for a financial product, and refusal rate is
 * its honest counterweight: silent-wrong is trivially driven to zero by refusing
 * everything, so the two must be read together.
 */

function deltaTone(d?: number | null) {
  if (d == null || Math.abs(d) < 0.005) return "default" as const;
  return d > 0 ? ("green" as const) : ("red" as const);
}

function Delta({ value, invert = false }: { value?: number | null; invert?: boolean }) {
  if (value == null) return <span className="muted">—</span>;
  const shown = (value > 0 ? "+" : "") + (value * 100).toFixed(1) + " pts";
  // For silent-wrong, down is good — invert the colour, not the sign.
  const tone = deltaTone(invert ? -value : value);
  return <Badge tone={tone}>{shown}</Badge>;
}

export default function EvalPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["evalRuns"],
    queryFn: () => api.evalRuns({ limit: 100 }),
  });
  const runs = data?.items ?? [];

  const [suite, setSuite] = useState("");
  const [system, setSystem] = useState("");
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");

  const suites = useMemo(() => [...new Set(runs.map((r) => r.suite))], [runs]);
  const systems = useMemo(() => [...new Set(runs.map((r) => r.system))], [runs]);

  const view = useMemo(
    () => runs.filter((r) => (!suite || r.suite === suite) && (!system || r.system === system)),
    [runs, suite, system],
  );
  // The headline is the latest *full* run: a partial run (--kind / --rerun-failed /
  // --sample) is over a deliberately-biased subset and is never a baseline.
  const latest: EvalRun | undefined = view.find((r) => r.tier !== "partial");

  // Deltas between consecutive *full* runs of the same suite+system. v_run_trend's
  // lag runs over every run, so a full run after a partial one would be compared
  // with the biased subset; recompute here, falling back to the view's values when
  // the previous full run is outside the loaded page.
  const trend = useMemo(() => {
    const out = new Map<string, Pick<EvalRun, "accuracy_delta" | "silent_wrong_delta" | "corpus_changed">>();
    runs.forEach((r, i) => {
      if (r.tier === "partial") return;
      const prev = runs
        .slice(i + 1)
        .find((p) => p.tier !== "partial" && p.suite === r.suite && p.system === r.system && p.status !== "RUNNING");
      if (!prev) return;
      const diff = (a?: number | null, b?: number | null) => (a == null || b == null ? null : a - b);
      out.set(r.run_id, {
        accuracy_delta: diff(r.accuracy, prev.accuracy),
        silent_wrong_delta: diff(r.silent_wrong_rate, prev.silent_wrong_rate),
        corpus_changed: (r.corpus_fingerprint ?? null) !== (prev.corpus_fingerprint ?? null),
      });
    });
    return out;
  }, [runs]);

  if (isLoading) return <p className="muted">Loading eval runs…</p>;
  if (error) return <Empty>Could not reach the eval store: {String(error)}</Empty>;

  return (
    <div>
      <h1 className="page-title">Evaluation</h1>
      <p className="page-sub">
        Answer quality measured against ground truth derived from the fact store itself.
        Every run is scored on the same committed suite, so numbers are comparable across
        runs — provided the corpus fingerprint matches.{" "}
        <Link to="/tech/eval/reliability">Noise band, silent-wrong queue &amp; flaky cases →</Link>
      </p>

      {!runs.length && (
        <Empty>
          No eval runs recorded yet. Generate a suite with{" "}
          <code>scripts/eval/build_sql_derived_suite.py</code> and run it with{" "}
          <code>scripts/eval/run_suite.py</code>.
        </Empty>
      )}

      {latest && (
        <Card
          title={`Latest full run — ${latest.suite}_${latest.suite_version ?? ""} · ${latest.system}`}
          hint={`${latest.n_cases} cases${latest.n_skipped ? ` (+${latest.n_skipped} not attempted)` : ""} · ${
            latest.corpus_fingerprint ?? "unknown corpus"
          }`}
        >
          <div className="tiles">
            <Tile label="Accuracy" value={pct(latest.accuracy)} />
            <Tile label="Silent-wrong" value={pct(latest.silent_wrong_rate)} />
            <Tile label="Confident-wrong" value={pct(latest.confident_wrong_rate)} />
            <Tile label="Value accuracy" value={pct(latest.value_accuracy)} />
            <Tile label="Refusal" value={pct(latest.refusal_rate)} />
            <Tile
              label="Declined"
              value={latest.decline_rate == null ? "—" : pct(latest.decline_rate)}
            />
            <Tile label="Citation" value={pct(latest.citation_rate)} />
            <Tile label="Grounding P" value={pct(latest.grounding_precision)} />
            <Tile label="Cost" value={usd(Number(latest.total_cost_usd))} />
            <Tile label="p95 latency" value={ms(latest.latency_p95_ms)} />
          </div>
          <p className="muted small">
            <strong>Silent-wrong</strong> is a confident, cited, wrong number — the failure
            a financial product cannot ship. Read it beside <strong>refusal</strong>: a system
            that declines everything scores zero silent-wrong while answering nothing.{" "}
            <strong>Confident-wrong</strong> drops the citation condition, so it applies to
            any system alike, and <strong>value accuracy</strong> counts right numbers whether
            or not they were cited. Both are blank for runs graded before grader version 3.
          </p>
        </Card>
      )}

      <div className="filterbar">
        <select value={suite} onChange={(e) => setSuite(e.target.value)}>
          <option value="">any suite</option>
          {suites.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">any system</option>
          {systems.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <span className="grow" />
        <select value={compareA} onChange={(e) => setCompareA(e.target.value)}>
          <option value="">compare A…</option>
          {view.map((r) => (
            <option key={r.run_id} value={r.run_id}>{r.run_id}</option>
          ))}
        </select>
        <select value={compareB} onChange={(e) => setCompareB(e.target.value)}>
          <option value="">compare B…</option>
          {view.map((r) => (
            <option key={r.run_id} value={r.run_id}>{r.run_id}</option>
          ))}
        </select>
        <button
          disabled={!compareA || !compareB || compareA === compareB}
          onClick={() => navigate(`/tech/eval/compare/${compareA}/${compareB}`)}
        >
          Diff
        </button>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Suite</th>
            <th>System</th>
            <th>Tier</th>
            <th className="num">Cases</th>
            <th className="num">Accuracy</th>
            <th className="num">Δ</th>
            <th className="num">Silent-wrong</th>
            <th className="num">Refusal</th>
            <th className="num">Cost</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {view.map((run) => {
            const r = { ...run, ...trend.get(run.run_id) };
            return (
            <tr key={r.run_id} className="clickable" onClick={() => navigate(`/tech/eval/${r.run_id}`)}>
              <td>
                <Link to={`/tech/eval/${r.run_id}`} onClick={(e) => e.stopPropagation()}>
                  {r.run_id}
                </Link>
                <div className="muted small">{new Date(r.created_at).toLocaleString()}</div>
              </td>
              <td>
                {r.suite}
                <span className="muted small"> {r.suite_version}</span>
              </td>
              <td>{r.system}</td>
              <td>
                {r.tier === "partial" ? (
                  <span title={r.notes ?? "narrowed run"}>
                    <Badge tone="amber">partial</Badge>
                  </span>
                ) : (
                  r.tier ?? "full"
                )}
              </td>
              <td className="num">
                {r.n_cases}
                {r.n_skipped ? <span className="muted small"> +{r.n_skipped}</span> : null}
              </td>
              <td className="num">{pct(r.accuracy)}</td>
              <td className="num">
                {/* A corpus change is not a quality change — never show a delta across
                    one. A partial run has no delta: its subset is not comparable. */}
                {r.tier === "partial" ? (
                  <span className="muted small">subset</span>
                ) : r.corpus_changed ? (
                  <Badge tone="amber">corpus changed</Badge>
                ) : (
                  <Delta value={r.accuracy_delta} />
                )}
              </td>
              <td className="num">
                {pct(r.silent_wrong_rate)}{" "}
                {!r.corpus_changed && r.tier !== "partial" && <Delta value={r.silent_wrong_delta} invert />}
              </td>
              <td className="num">{pct(r.refusal_rate)}</td>
              <td className="num">{usd(Number(r.total_cost_usd))}</td>
              <td>
                <Badge tone={r.status === "OK" ? "green" : r.status === "ABORTED" ? "amber" : "red"}>
                  {r.status}
                </Badge>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
