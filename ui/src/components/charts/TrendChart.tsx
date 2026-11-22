import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS, ChartFrame, ChartLegend, ChartTooltip, GRID, seriesColor } from "./shared";

export interface TrendSeries {
  key: string;
  label: string;
  colorIndex?: number;
}

// One-axis time-series. Multiple series share a single y-scale (never a dual axis).
export function TrendChart({
  data,
  xKey,
  series,
  height = 200,
  area = false,
  yFmt = (v) => String(v),
  title,
  sub,
}: {
  data: Array<Record<string, unknown>>;
  xKey: string;
  series: TrendSeries[];
  height?: number;
  area?: boolean;
  yFmt?: (v: number | string) => string;
  title?: string;
  sub?: string;
}) {
  const colorOf = (s: TrendSeries, i: number) => seriesColor(s.colorIndex ?? i);
  const legend = series.map((s, i) => ({ label: s.label, color: colorOf(s, i) }));

  const body = area ? (
    <AreaChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
      <defs>
        {series.map((s, i) => (
          <linearGradient id={`grad-${s.key}`} key={s.key} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colorOf(s, i)} stopOpacity={0.35} />
            <stop offset="100%" stopColor={colorOf(s, i)} stopOpacity={0.02} />
          </linearGradient>
        ))}
      </defs>
      <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
      <XAxis dataKey={xKey} stroke={AXIS} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={24} />
      <YAxis stroke={AXIS} tickLine={false} axisLine={false} width={64} tickFormatter={(v) => yFmt(v)} />
      <Tooltip content={ChartTooltip(yFmt)} cursor={{ stroke: GRID }} />
      {series.map((s, i) => (
        <Area
          key={s.key}
          type="monotone"
          dataKey={s.key}
          name={s.label}
          stroke={colorOf(s, i)}
          strokeWidth={2}
          fill={`url(#grad-${s.key})`}
          dot={false}
          activeDot={{ r: 4 }}
        />
      ))}
    </AreaChart>
  ) : (
    <LineChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
      <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
      <XAxis dataKey={xKey} stroke={AXIS} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={24} />
      <YAxis stroke={AXIS} tickLine={false} axisLine={false} width={64} tickFormatter={(v) => yFmt(v)} />
      <Tooltip content={ChartTooltip(yFmt)} cursor={{ stroke: GRID }} />
      {series.map((s, i) => (
        <Line
          key={s.key}
          type="monotone"
          dataKey={s.key}
          name={s.label}
          stroke={colorOf(s, i)}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      ))}
    </LineChart>
  );

  return (
    <ChartFrame title={title} sub={sub}>
      <ResponsiveContainer width="100%" height={height}>
        {body}
      </ResponsiveContainer>
      <ChartLegend items={legend} />
    </ChartFrame>
  );
}
