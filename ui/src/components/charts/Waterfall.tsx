import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS, ChartFrame, GRID } from "./shared";

export interface BridgeItem {
  label: string;
  value: number; // totals: absolute; deltas: signed change
  kind: "total" | "delta";
  onClick?: () => void;
}

// Variance-bridge waterfall. Totals (prior / current) anchor to zero in neutral gray;
// signed deltas float on a running base — increases green, decreases red (diverging).
// Rendered via recharts stacked bars: an invisible `base` offset + a visible `bar`.
export function Waterfall({
  items,
  height = 260,
  valueFmt = (v) => String(v),
  title,
  sub,
}: {
  items: BridgeItem[];
  height?: number;
  valueFmt?: (v: number | string) => string;
  title?: string;
  sub?: string;
}) {
  let run = 0;
  const data = items.map((it) => {
    if (it.kind === "total") {
      run = it.value;
      return { ...it, base: 0, bar: it.value, tone: "total" as const, signed: it.value };
    }
    const before = run;
    const after = run + it.value;
    run = after;
    return {
      ...it,
      base: Math.min(before, after),
      bar: Math.abs(it.value),
      tone: it.value >= 0 ? ("up" as const) : ("down" as const),
      signed: it.value,
    };
  });

  const fillFor = (tone: string) =>
    tone === "total" ? "var(--chart-axis)" : tone === "up" ? "var(--green)" : "var(--red)";

  return (
    <ChartFrame title={title} sub={sub}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 16, right: 10, bottom: 4, left: 6 }}>
          <XAxis dataKey="label" stroke={AXIS} tickLine={false} axisLine={{ stroke: GRID }} interval={0} angle={-18} textAnchor="end" height={54} />
          <YAxis stroke={AXIS} tickLine={false} axisLine={false} width={54} tickFormatter={(v) => valueFmt(v)} />
          <Tooltip
            cursor={{ fill: "var(--bg-elev2)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as { label: string; signed: number; kind: string };
              return (
                <div className="chart-tooltip">
                  <div className="tt-label">{d.label}</div>
                  <div className="tt-row">
                    <span className="tt-val">{(d.kind === "delta" && d.signed >= 0 ? "+" : "") + valueFmt(d.signed)}</span>
                  </div>
                </div>
              );
            }}
          />
          <Bar dataKey="base" stackId="wf" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="bar" stackId="wf" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={fillFor(d.tone)} cursor={d.onClick ? "pointer" : undefined} onClick={() => d.onClick?.()} />
            ))}
            <LabelList
              dataKey="signed"
              position="top"
              formatter={(v: number) => valueFmt(v)}
              style={{ fill: "var(--text-dim)", fontSize: 10, fontFamily: "var(--mono)" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
