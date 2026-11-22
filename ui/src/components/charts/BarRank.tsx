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
import { AXIS, ChartFrame, ChartTooltip, GRID, seriesColor } from "./shared";

export interface RankRow {
  label: string;
  value: number;
  onClick?: () => void;
}

// Horizontal ranked bars — for "which companies / stages…" magnitude comparisons.
// Single measure => single hue (no legend). Rows are click-through to drill-down.
export function BarRank({
  rows,
  height,
  valueFmt = (v) => String(v),
  colorIndex = 0,
  title,
  sub,
  onRowClick,
}: {
  rows: RankRow[];
  height?: number;
  valueFmt?: (v: number | string) => string;
  colorIndex?: number;
  title?: string;
  sub?: string;
  onRowClick?: (row: RankRow, index: number) => void;
}) {
  const h = height ?? Math.max(120, rows.length * 34 + 20);
  const color = seriesColor(colorIndex);
  return (
    <ChartFrame title={title} sub={sub}>
      <ResponsiveContainer width="100%" height={h}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 8 }}>
          <XAxis type="number" hide tickFormatter={(v) => valueFmt(v)} />
          <YAxis
            type="category"
            dataKey="label"
            width={110}
            stroke={AXIS}
            tickLine={false}
            axisLine={{ stroke: GRID }}
          />
          <Tooltip content={ChartTooltip(valueFmt)} cursor={{ fill: "var(--bg-elev2)" }} />
          <Bar
            dataKey="value"
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
            cursor={onRowClick ? "pointer" : undefined}
            onClick={(_d, i) => onRowClick?.(rows[i], i)}
          >
            {rows.map((_r, i) => (
              <Cell key={i} fill={color} />
            ))}
            <LabelList
              dataKey="value"
              position="right"
              formatter={(v: number) => valueFmt(v)}
              style={{ fill: "var(--text-dim)", fontSize: 11, fontFamily: "var(--mono)" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
