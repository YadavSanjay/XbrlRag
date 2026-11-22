import { useEffect, type ReactNode } from "react";

/** Right-hand drill-down panel. Overview elements (rows/cells/bars) open this to
 *  reveal the underlying facts, citations, or events. Reused across screens. */
export function SlideOver({
  title,
  onClose,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="slideover-scrim no-print" onClick={onClose} />
      <aside className="slideover no-print" role="dialog" aria-modal="true">
        <div className="slideover-head">
          <h3>{title}</h3>
          <button className="slideover-close" onClick={onClose} title="Close (Esc)">
            ×
          </button>
        </div>
        <div className="slideover-body">{children}</div>
      </aside>
    </>
  );
}

/** Definition-list detail rows for drill-down panels. */
export function KV({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <div style={{ display: "contents" }} key={i}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Render an XBRL fact citation dict compactly — known fields as rows, else JSON. */
export function Citation({ value }: { value?: Record<string, unknown> | null }) {
  if (!value || typeof value !== "object") return <span className="dim">no citation</span>;
  const known: Array<[string, string]> = [
    ["qname", "Concept"],
    ["value", "Value"],
    ["unit", "Unit"],
    ["fiscal_year", "Fiscal year"],
    ["period_end", "Period end"],
    ["form_type", "Form"],
    ["accession_no", "Accession"],
    ["filed_date", "Filed"],
    ["page_num", "Page"],
    ["statement", "Statement"],
    // HTML-canonical source: where the fact sits in the filing's canonical HTML.
    ["section_path", "Section"],
    ["html_anchor", "Anchor"],
    ["fact_id", "Fact id"],
  ];
  const rows = known
    .filter(([k]) => value[k] != null && value[k] !== "")
    .map(([k, label]) => [label, String(value[k])] as [string, ReactNode]);
  if (!rows.length) {
    return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
  }
  return <KV rows={rows} />;
}

export function Card({ title, hint, children }: { title?: string; hint?: string; children: ReactNode }) {
  return (
    <div className="card">
      {title && <h3>{title}</h3>}
      {hint && <div className="card-hint">{hint}</div>}
      {children}
    </div>
  );
}

export function Tile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

type Tone = "green" | "amber" | "red" | "blue" | "purple" | "default";

export function Badge({ tone = "default", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone === "default" ? "" : tone}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** A sortable table header cell. `sortKey` matches the active key to show the arrow. */
export function SortTh({
  label, col, active, dir, onSort,
}: {
  label: string;
  col: string;
  active: string;
  dir: "asc" | "desc";
  onSort: (col: string) => void;
}) {
  return (
    <th className="sortable" onClick={() => onSort(col)}>
      {label}{active === col ? (dir === "desc" ? " ↓" : " ↑") : ""}
    </th>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function statusTone(status?: string): Tone {
  const s = (status ?? "").toUpperCase();
  if (["OK", "SUCCESS"].includes(s)) return "green";
  // Normal non-error outcomes — a missing fact, an ambiguous match, an
  // unresolved dimension, a degraded fallback — are amber, not alarm-red.
  // Red (soft danger) is reserved for genuine failures only.
  if (["PARTIAL", "AMBIGUOUS", "DEGRADED", "NOT_FOUND", "UNRESOLVED_DIMENSION", "NOT_MEANINGFUL"].includes(s)) return "amber";
  if (["ERROR", "FAILED"].includes(s)) return "red";
  return "default";
}

export function ms(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  return `${(n / 1000).toFixed(2)} s`;
}

export function usd(n?: number | null): string {
  if (n == null) return "—";
  return `$${n.toFixed(4)}`;
}

/** A 0–1 rate (how the eval schema stores accuracy, refusal, …) as a percentage. */
export function pct(n?: number | string | null, dp = 1): string {
  if (n == null || n === "") return "—";
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(dp)}%`;
}

export function num(n?: number | null): string {
  if (n == null) return "—";
  return Math.round(n).toLocaleString();
}
