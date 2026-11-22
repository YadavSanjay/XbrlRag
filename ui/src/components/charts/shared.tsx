import type { ReactNode } from "react";

// Categorical series colors — resolved from CSS custom properties so they follow
// the active light/dark theme automatically. Assigned in fixed order, never cycled;
// a 9th series folds into "Other" upstream. See docs/design/XbrlRag_UI_Design.md.
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
] as const;

export const seriesColor = (i: number): string => SERIES[i % SERIES.length];

export const AXIS = "var(--chart-axis)";
export const GRID = "var(--chart-grid)";

// ---- value formatters ----
export function fmtUsd(n?: number | null, dp = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(dp)}`;
}

export function fmtUsdFull(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toFixed(4)}`;
}

export function fmtInt(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString();
}

export function fmtCompact(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

export function fmtMs(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  return `${(n / 1000).toFixed(2)} s`;
}

// ---- themed tooltip (recharts `content` render prop) ----
type TipRow = { name?: string; value?: number | string; color?: string };
export function ChartTooltip(fmt: (v: number | string) => string) {
  return function Tip({ active, payload, label }: {
    active?: boolean;
    payload?: Array<{ name?: string; value?: number | string; color?: string; payload?: Record<string, unknown> }>;
    label?: string | number;
  }) {
    if (!active || !payload || !payload.length) return null;
    return (
      <div className="chart-tooltip">
        {label != null && label !== "" && <div className="tt-label">{String(label)}</div>}
        {payload.map((p: TipRow, i: number) => (
          <div className="tt-row" key={i}>
            {p.color && <span className="tt-swatch" style={{ background: p.color }} />}
            {p.name && <span className="dim">{p.name}</span>}
            <span className="tt-val">{p.value != null ? fmt(p.value) : "—"}</span>
          </div>
        ))}
      </div>
    );
  };
}

export function ChartLegend({ items }: { items: Array<{ label: string; color: string }> }) {
  if (items.length < 2) return null;
  return (
    <div className="chart-legend">
      {items.map((it) => (
        <span className="lg-item" key={it.label}>
          <span className="lg-swatch" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

export function ChartFrame({ title, sub, children }: { title?: string; sub?: string; children: ReactNode }) {
  return (
    <div className="chart-wrap">
      {title && <div className="chart-title">{title}</div>}
      {sub && <div className="chart-sub">{sub}</div>}
      {children}
    </div>
  );
}
