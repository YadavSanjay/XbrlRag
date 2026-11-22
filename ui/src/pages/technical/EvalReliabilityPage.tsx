import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { summarizeRepeats, type RepeatSummary } from "../../lib/repeats";
import { Badge, Card, Empty, Tile, pct } from "../../components/ui";
import type { EvalRun } from "../../lib/types";

/**
 * Is a change real or noise? One run of the suite moves a point or two between
 * identical runs (planner non-determinism), so a number is only meaningful against
 * the noise band. This page puts the three reliability views in one place:
 *  - repeat spread over runs you pick (docs/specs/repeat-spread.md),
 *  - the silent-wrong queue across all runs (the release gate),
 *  - cases whose grade flips between runs.
 */

function pts(x: number): string {
  return `${(x * 100).toFixed(2)} pts`;
}

export default function EvalReliabilityPage() {
  const runsQ = useQuery({ queryKey: ["evalRuns"], queryFn: () => api.evalRuns({ limit: 100 }) });
  const runs = useMemo(() => (runsQ.data?.items ?? []).filter((r) => r.status !== "RUNNING"), [runsQ.data]);
  const suites = useMemo(() => [...new Set(runs.map((r) => r.suite))], [runs]);
  const [suite, setSuite] = useState("");
  const activeSuite = suite || suites[0] || "";

  return (
    <div>
      <h1 className="page-title">Eval reliability</h1>
      <p className="page-sub">
        Read a new number against the noise band. A single full run moves about a point
        between identical runs, so a change under ~2 points needs repeats before it is
        believed. <Link to="/tech/eval">← run history</Link>
      </p>

      {runsQ.error ? <Empty>Could not reach the eval store: {String(runsQ.error)}</Empty> : null}

      <div className="filterbar">
        <select value={activeSuite} onChange={(e) => setSuite(e.target.value)}>
          {suites.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <RepeatSpread runs={runs.filter((r) => r.suite === activeSuite)} />
      <SilentWrongQueue />
      <FlakyCases suite={activeSuite} />
    </div>
  );
}

function RepeatSpread({ runs }: { runs: EvalRun[] }) {
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const cases = useQueries({
    queries: picked.map((runId) => ({
      queryKey: ["evalCasesAll", runId],
      queryFn: () => api.evalCases(runId, { limit: 5000 }),
    })),
  });
  const loading = cases.some((c) => c.isLoading);
  const failed = cases.find((c) => c.error);

  // Cheap (a few hundred cases × N runs), so computed on every render.
  let result: { summary?: RepeatSummary; error?: string } = {};
  if (picked.length >= 2 && !loading && !failed) {
    const kinds: Record<string, string> = {};
    const passMaps = cases.map((c) => {
      const m: Record<string, boolean> = {};
      for (const row of c.data?.items ?? []) {
        m[row.case_id] = row.passed;
        if (row.kind) kinds[row.case_id] = row.kind;
      }
      return m;
    });
    try {
      result = { summary: summarizeRepeats(passMaps, kinds) };
    } catch (e) {
      result = { error: e instanceof Error ? e.message : String(e) };
    }
  }

  const chosen = runs.filter((r) => picked.includes(r.run_id));
  const graders = new Set(chosen.map((r) => r.grader_version ?? "unversioned"));
  const corpora = new Set(chosen.map((r) => r.corpus_fingerprint ?? "unknown"));
  const s = result.summary;

  return (
    <Card
      title="Repeat spread"
      hint="pick two or more runs of the same cases — a full run plus slice repeats is fine; only common cases count"
    >
      <div className="scroll-y" style={{ maxHeight: 240 }}>
        <table className="table compact">
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td>
                  <input
                    type="checkbox"
                    checked={picked.includes(r.run_id)}
                    onChange={() => toggle(r.run_id)}
                    aria-label={`include ${r.run_id}`}
                  />
                </td>
                <td><Link to={`/tech/eval/${r.run_id}`}>{r.run_id}</Link></td>
                <td className="muted small">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.tier === "partial" ? <Badge tone="amber">partial</Badge> : <Badge tone="default">full</Badge>}</td>
                <td className="num">{r.n_cases}</td>
                <td className="num">{pct(r.accuracy)}</td>
                <td className="muted small mono">{r.git_sha?.slice(0, 8) ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {picked.length < 2 ? (
        <p className="muted mt-8 mb-0">Select at least two runs.</p>
      ) : loading ? (
        <p className="muted mt-8 mb-0">Loading case grades…</p>
      ) : failed ? (
        <Empty>Could not load cases: {String(failed.error)}</Empty>
      ) : result.error ? (
        <Empty>Cannot summarise: {result.error}</Empty>
      ) : s ? (
        <>
          {graders.size > 1 || corpora.size > 1 ? (
            <p className="mt-8">
              <Badge tone="amber">not commensurable</Badge>{" "}
              The selected runs differ in{" "}
              {[graders.size > 1 ? "grader version" : null, corpora.size > 1 ? "corpus" : null]
                .filter(Boolean)
                .join(" and ")}
              . Re-grade them together with <code>scripts/eval/summarize_repeats.py</code> before
              reading the spread.
            </p>
          ) : null}
          <div className="tiles mt-8">
            <Tile label="Mean ± sd" value={`${pct(s.mean)} ± ${pts(s.std)}`} />
            <Tile label="Min – max" value={`${pct(s.min)} – ${pct(s.max)}`} />
            <Tile label="Floor" value={pct(s.floor)} />
            <Tile label="Ceiling" value={pct(s.ceiling)} />
            <Tile label="Flip rate" value={pct(s.flip_rate)} />
            <Tile label="Common cases" value={`${s.n_cases} × ${s.n_runs} runs`} />
          </div>
          <p className="muted small">
            Uses each run's <strong>stored</strong> grades. <strong>Floor</strong> = passed in every
            run; <strong>ceiling</strong> = passed at least once. The CLI re-grades every run with
            today's graders first — use it after a grader or ground-truth change.
          </p>
          <div className="grid-2">
            <Card title="By kind">
              <table className="table compact">
                <thead>
                  <tr><th>Kind</th><th className="num">n</th><th className="num">Mean ± sd</th><th className="num">Floor–ceiling</th><th className="num">Flaky</th></tr>
                </thead>
                <tbody>
                  {Object.entries(s.by_kind).map(([kind, k]) => (
                    <tr key={kind}>
                      <td>{kind}</td>
                      <td className="num">{k.n_cases}</td>
                      <td className="num">{pct(k.mean)} ± {pts(k.std)}</td>
                      <td className="num">{pct(k.floor)} – {pct(k.ceiling)}</td>
                      <td className="num">{k.flaky_case_ids.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title={`Flaky cases (${s.flaky_case_ids.length})`} hint="passed in some selected runs, failed in others">
              <div className="scroll-y" style={{ maxHeight: 280 }}>
                {s.flaky_case_ids.length ? (
                  <ul className="mono small" style={{ margin: 0, paddingLeft: 18 }}>
                    {s.flaky_case_ids.map((c) => <li key={c}>{c}</li>)}
                  </ul>
                ) : (
                  <p className="muted mb-0">None — every common case got the same grade in every run.</p>
                )}
              </div>
            </Card>
          </div>
        </>
      ) : null}
    </Card>
  );
}

function SilentWrongQueue() {
  const q = useQuery({ queryKey: ["evalSilentWrong"], queryFn: () => api.evalSilentWrong(100) });
  const rows = q.data?.items ?? [];
  return (
    <Card
      title={`Silent-wrong queue (${rows.length})`}
      hint="confident, cited and wrong — across every run, newest first; the release gate"
    >
      {q.isLoading ? (
        <p className="muted mb-0">Loading…</p>
      ) : !rows.length ? (
        <p className="muted mb-0">No silent-wrong cases recorded.</p>
      ) : (
        <div className="scroll-y" style={{ maxHeight: 360 }}>
          <table className="table compact">
            <thead>
              <tr><th>Run</th><th>Case</th><th>Question</th><th>Expected</th><th>Produced</th><th>Trace</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.run_id}:${r.case_id}`}>
                  <td><Link to={`/tech/eval/${r.run_id}`}>{r.run_id}</Link></td>
                  <td className="mono small">{r.case_id}</td>
                  <td>
                    {r.question}
                    {r.reason ? <div className="muted small">{r.reason}</div> : null}
                  </td>
                  <td className="mono small">{JSON.stringify(r.expected_value ?? null)}</td>
                  <td className="mono small">{JSON.stringify(r.produced_values ?? null)?.slice(0, 80)}</td>
                  <td>{r.query_id ? <Link to={`/tech/traces/${r.query_id}`}>trace →</Link> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function FlakyCases({ suite }: { suite: string }) {
  const q = useQuery({
    queryKey: ["evalFlaky", suite],
    queryFn: () => api.evalFlaky({ suite: suite || undefined, limit: 200 }),
  });
  const rows = q.data?.items ?? [];
  return (
    <Card
      title={`Flaky across history (${rows.length}${rows.length === 200 ? "+" : ""})`}
      hint="every stored run of this suite — includes cases fixed or broken by code changes, not only noise"
    >
      {q.isLoading ? (
        <p className="muted mb-0">Loading…</p>
      ) : !rows.length ? (
        <p className="muted mb-0">No case has changed grade between runs.</p>
      ) : (
        <div className="scroll-y" style={{ maxHeight: 360 }}>
          <table className="table compact">
            <thead>
              <tr><th>Case</th><th>System</th><th className="num">Passed</th><th className="num">Pass rate</th><th>Question</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.system}:${r.case_id}`}>
                  <td className="mono small">{r.case_id}</td>
                  <td>{r.system}</td>
                  <td className="num">{r.n_passed}/{r.n_runs}</td>
                  <td className="num">{pct(r.pass_rate)}</td>
                  <td className="small">{r.question}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
