import { ms } from "./ui";

/** Stage waterfall (Gantt) built from query_audit.stage_latency_ms. */
export default function StageWaterfall({ stages }: { stages: Record<string, number> }) {
  const entries = Object.entries(stages ?? {});
  if (!entries.length) return <p className="dim">No per-stage timing recorded.</p>;

  // Sequential layout: each bar starts where the previous ended (approx flow).
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  let acc = 0;
  return (
    <div className="waterfall">
      {entries.map(([name, dur]) => {
        const left = (acc / total) * 100;
        const width = Math.max((dur / total) * 100, 1.5);
        acc += dur;
        return (
          <div className="wf-row" key={name}>
            <div className="wf-label">{name}</div>
            <div className="wf-track">
              <div className="wf-bar" style={{ left: `${left}%`, width: `${width}%` }} />
            </div>
            <div className="wf-ms">{ms(dur)}</div>
          </div>
        );
      })}
    </div>
  );
}
