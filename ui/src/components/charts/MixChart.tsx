import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { ChartFrame, ChartTooltip, seriesColor } from "./shared";

export interface MixSlice {
  label: string;
  value: number;
  onClick?: () => void;
}

// Donut for share/mix (branch mix, geo/segment split). Categorical hues in fixed
// order. A 9th+ slice should be folded into "Other" by the caller before passing in.
export function MixChart({
  slices,
  height = 200,
  valueFmt = (v) => String(v),
  title,
  sub,
}: {
  slices: MixSlice[];
  height?: number;
  valueFmt?: (v: number | string) => string;
  title?: string;
  sub?: string;
}) {
  const total = slices.reduce((a, s) => a + (s.value || 0), 0);
  return (
    <ChartFrame title={title} sub={sub}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <ResponsiveContainer width={height} height={height}>
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="label"
              innerRadius="58%"
              outerRadius="92%"
              paddingAngle={2}
              stroke="var(--chart-surface)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {slices.map((s, i) => (
                <Cell key={i} fill={seriesColor(i)} cursor={s.onClick ? "pointer" : undefined} onClick={() => s.onClick?.()} />
              ))}
            </Pie>
            <Tooltip content={ChartTooltip(valueFmt)} />
          </PieChart>
        </ResponsiveContainer>
        <div className="chart-legend" style={{ flexDirection: "column", gap: 6, marginTop: 0 }}>
          {slices.map((s, i) => (
            <span
              className="lg-item"
              key={s.label}
              style={{ cursor: s.onClick ? "pointer" : undefined }}
              onClick={() => s.onClick?.()}
            >
              <span className="lg-swatch" style={{ background: seriesColor(i) }} />
              {s.label}
              <span className="dim" style={{ fontFamily: "var(--mono)" }}>
                {total ? `${((s.value / total) * 100).toFixed(0)}%` : ""}
              </span>
            </span>
          ))}
        </div>
      </div>
    </ChartFrame>
  );
}
