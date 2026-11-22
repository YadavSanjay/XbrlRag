import { seriesColor } from "./shared";

// Tiny inline trend for stat tiles and table rows. Pure SVG (no recharts overhead
// for the many-instances case). Last point gets a dot.
export function Sparkline({
  values,
  width = 88,
  height = 24,
  colorIndex = 0,
}: {
  values: Array<number | null | undefined>;
  width?: number;
  height?: number;
  colorIndex?: number;
}) {
  const nums = values.map((v) => (v == null || Number.isNaN(v) ? null : v));
  const present = nums.filter((v): v is number => v != null);
  if (present.length < 2) return <span className="dim mono">—</span>;
  const min = Math.min(...present);
  const max = Math.max(...present);
  const span = max - min || 1;
  const n = nums.length;
  const x = (i: number) => (n === 1 ? 0 : (i / (n - 1)) * (width - 2) + 1);
  const y = (v: number) => height - 2 - ((v - min) / span) * (height - 4);

  let d = "";
  let started = false;
  nums.forEach((v, i) => {
    if (v == null) return;
    d += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
    started = true;
  });
  const lastIdx = nums.reduce<number>((acc, v, i) => (v != null ? i : acc), -1);
  const lastVal = lastIdx >= 0 ? nums[lastIdx] : null;
  const color = seriesColor(colorIndex);

  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={d.trim()} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      {lastVal != null && <circle cx={x(lastIdx)} cy={y(lastVal)} r={2.2} fill={color} />}
    </svg>
  );
}
