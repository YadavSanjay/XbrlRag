import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Badge, Card, Empty, Json, KV, Tile, ms, pct, usd } from "../../components/ui";
import type { EvalCaseRow } from "../../lib/types";

/**
 * WS-9 / E4 — one run in full fidelity.
 *
 * The design's key reuse: every case row links to `/tech/traces/<query_id>`, so a
 * failing number drills straight into the existing Trace Explorer — plan, exact SQL,
 * every LLM prompt and its cost. Nothing new had to be instrumented for that.
 */

// Follows statusTone()'s house rule: amber for a normal non-error outcome (a
// partially-supported answer), red only for a genuine failure. REFUSED stays neutral
// — declining rather than inventing is the designed behaviour, not a defect. A
// HEDGED_MISS is a decline that showed the nearest facts it had, so it is neutral too.
// IMPRECISE is the key rounded at a coarser place than its rule: amber, not a failure.
const GRADE_TONE: Record<string, "green" | "amber" | "red" | "default"> = {
  CORRECT: "green",
  PARTIAL: "amber",
  INCORRECT: "red",
  REFUSED: "default",
  HEDGED_MISS: "default",
  IMPRECISE: "amber",
  ERROR: "red",
};

const GRADES = [
  "CORRECT", "PARTIAL", "IMPRECISE", "INCORRECT", "REFUSED", "HEDGED_MISS", "ERROR",
];

function AccuracyBar({ value }: { value: number }) {
  const p = Math.max(0, Math.min(1, value));
  return (
    <div className="bar">
      <div className="bar-fill" style={{ width: `${p * 100}%` }} />
    </div>
  );
}

function CaseDetail({ c }: { c: EvalCaseRow }) {
  const expected = (c.expected ?? {}) as Record<string, unknown>;
  const produced = (c.produced ?? {}) as Record<string, unknown>;
  const expectedFacts = (expected.fact_ids as number[] | undefined) ?? [];
  const producedFacts = (produced.fact_ids as number[] | undefined) ?? [];
  const missing = expectedFacts.filter((f) => !producedFacts.includes(f));
  const extra = producedFacts.filter((f) => !expectedFacts.includes(f));

  return (
    <div className="case-detail">
      <KV
        rows={[
          ["Reason", c.reason ?? "—"],
          ["Origin", <code key="o">{c.origin ?? "—"}</code>],
          ["Expected", <Json key="e" value={expected} />],
          ["Produced", <Json key="p" value={produced} />],
          [
            "Fact grounding",
            <span key="g">
              {expectedFacts.length ? (
                <>
                  expected <code>[{expectedFacts.join(", ")}]</code>
                  {missing.length > 0 && (
                    <>
                      {" "}
                      · <Badge tone="red">missing [{missing.join(", ")}]</Badge>
                    </>
                  )}
                  {extra.length > 0 && (
                    <>
                      {" "}
                      · <Badge tone="amber">extra [{extra.join(", ")}]</Badge>
                    </>
                  )}
                </>
              ) : (
                <span className="muted">not asserted for this case</span>
              )}
            </span>,
          ],
          ...(c.screen_metrics
            ? ([["Ranking / coverage", <Json key="s" value={c.screen_metrics} />]] as Array<
                [string, React.ReactNode]
              >)
            : []),
          [
            "Trace",
            c.query_id ? (
              <Link to={`/tech/traces/${c.query_id}`}>{c.query_id}</Link>
            ) : (
              <span className="muted">
                not persisted — re-run with <code>--persist-audit</code> to link traces
              </span>
            ),
          ],
        ]}
      />
    </div>
  );
}

export default function EvalRunPage() {
  const { runId = "" } = useParams();
  const detail = useQuery({ queryKey: ["evalRun", runId], queryFn: () => api.evalRun(runId) });
  const [grade, setGrade] = useState("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const cases = useQuery({
    queryKey: ["evalCases", runId, grade, kind],
    queryFn: () => api.evalCases(runId, { grade: grade || undefined, kind: kind || undefined }),
  });

  const rows = useMemo(() => {
    const all = cases.data?.items ?? [];
    if (!q) return all;
    const needle = q.toLowerCase();
    return all.filter(
      (c) =>
        c.case_id.toLowerCase().includes(needle) ||
        (c.question ?? "").toLowerCase().includes(needle) ||
        (c.reason ?? "").toLowerCase().includes(needle),
    );
  }, [cases.data, q]);

  if (detail.isLoading) return <p className="muted">Loading run…</p>;
  const run = detail.data?.run;
  if (!run) return <Empty>Run {runId} not found.</Empty>;

  const byKind = detail.data?.by_kind ?? [];
  const byTicker = detail.data?.by_ticker ?? [];
  const instability = detail.data?.instability ?? [];

  return (
    <div>
      <h1 className="page-title">
        {run.suite}_{run.suite_version} · {run.system}
      </h1>
      <p className="page-sub">
        <code>{run.run_id}</code> · {new Date(run.created_at).toLocaleString()} · corpus{" "}
        <code>{run.corpus_fingerprint ?? "unknown"}</code>
        {run.git_sha ? (
          <>
            {" "}
            · git <code>{run.git_sha}</code>
          </>
        ) : null}
        {run.grader_version ? (
          <>
            {" "}
            · grader <code>{run.grader_version}</code>
          </>
        ) : null}
      </p>

      {run.tier === "partial" ? (
        <Card>
          <Badge tone="amber">partial run</Badge>{" "}
          {run.notes ? <code>{run.notes}</code> : "a narrowed selection of cases"}. Its accuracy
          is over a deliberately-biased subset, so it is not a baseline and is not comparable
          with a full run. Use it beside repeats on the{" "}
          <Link to="/tech/eval/reliability">reliability page</Link>, then take one full run.
        </Card>
      ) : null}

      <div className="tiles">
        <Tile label="Accuracy" value={pct(run.accuracy)} />
        <Tile label="Silent-wrong" value={pct(run.silent_wrong_rate)} />
        <Tile label="Confident-wrong" value={pct(run.confident_wrong_rate)} />
        <Tile label="Value accuracy" value={pct(run.value_accuracy)} />
        <Tile
          label="Imprecise"
          value={run.imprecise_rate == null ? "—" : pct(run.imprecise_rate)}
        />
        <Tile label="Refusal" value={pct(run.refusal_rate)} />
        <Tile
          label="Declined"
          value={run.decline_rate == null ? "—" : pct(run.decline_rate)}
        />
        <Tile label="Citation" value={pct(run.citation_rate)} />
        <Tile label="Grounding P/R" value={`${pct(run.grounding_precision)} / ${pct(run.grounding_recall)}`} />
        <Tile
          label="Paraphrase stability"
          value={run.paraphrase_stability == null ? "—" : pct(run.paraphrase_stability)}
        />
        <Tile label="Cost" value={usd(Number(run.total_cost_usd))} />
        <Tile label="p50 / p95" value={`${ms(run.latency_p50_ms)} / ${ms(run.latency_p95_ms)}`} />
      </div>

      <div className="grid-2">
        <Card title="By question kind" hint="Where the pipeline is strong and where it is not">
          <table className="table compact">
            <tbody>
              {byKind.map((k) => (
                <tr key={k.kind}>
                  <td>{k.kind}</td>
                  <td className="num">
                    {k.n_correct}/{k.n}
                  </td>
                  <td className="num">{pct(k.accuracy)}</td>
                  <td className="grow">
                    <AccuracyBar value={k.accuracy ?? 0} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="By filer × fiscal year" hint="A systemic weakness shows up as a row or column">
          <div className="scroll-y">
            <table className="table compact">
              <tbody>
                {byTicker.map((t) => (
                  <tr key={`${t.ticker}-${t.fy}`}>
                    <td>
                      {t.ticker} <span className="muted small">FY{t.fy}</span>
                    </td>
                    <td className="num">
                      {t.n_correct}/{t.n}
                    </td>
                    <td className="num">{pct(Number(t.accuracy))}</td>
                    <td className="grow">
                      <AccuracyBar value={Number(t.accuracy) || 0} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {instability.length > 0 && (
        <Card
          title={`Paraphrase instability (${instability.length})`}
          hint="Same ground truth, different phrasing, different verdict — routing non-determinism"
        >
          <table className="table compact">
            <tbody>
              {instability.map((g) => (
                <tr key={g.paraphrase_group}>
                  <td>{g.example_question}</td>
                  <td className="num">
                    {g.n_passed}/{g.n_variants} passed
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="filterbar">
        <input
          className="grow"
          placeholder="Search case id / question / reason…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select value={grade} onChange={(e) => setGrade(e.target.value)}>
          <option value="">any grade</option>
          {GRADES.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">any kind</option>
          {byKind.map((k) => (
            <option key={k.kind} value={k.kind}>{k.kind}</option>
          ))}
        </select>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Case</th>
            <th>Kind</th>
            <th>Grade</th>
            <th>Question</th>
            <th>Reason</th>
            <th>Trace</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <>
              <tr
                key={c.case_id}
                className="clickable"
                onClick={() => setOpen(open === c.case_id ? null : c.case_id)}
              >
                <td>
                  <code className="small">{c.case_id}</code>
                </td>
                <td>{c.kind}</td>
                <td>
                  <Badge tone={GRADE_TONE[c.grade] ?? "default"}>{c.grade}</Badge>
                  {c.silent_wrong && <Badge tone="red">silent-wrong</Badge>}
                  {c.confident_wrong && !c.silent_wrong && (
                    <Badge tone="red">confident-wrong</Badge>
                  )}
                  {c.needs_review && <Badge tone="amber">needs review</Badge>}
                </td>
                <td>{c.question}</td>
                <td className="small muted">{c.reason}</td>
                <td>
                  {c.query_id ? (
                    <Link to={`/tech/traces/${c.query_id}`} onClick={(e) => e.stopPropagation()}>
                      trace
                    </Link>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
              {open === c.case_id && (
                <tr key={`${c.case_id}-detail`}>
                  <td colSpan={6}>
                    <CaseDetail c={c} />
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
      {!rows.length && <Empty>No cases match this filter.</Empty>}
    </div>
  );
}
