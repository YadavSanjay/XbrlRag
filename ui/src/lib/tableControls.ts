// Small client-side helpers for filterable/sortable tables (Traces, History, …).
export type SortDir = "asc" | "desc";

export function sortRows<T>(
  rows: T[],
  accessor: (r: T) => number | string | null | undefined,
  dir: SortDir,
): T[] {
  const s = [...rows].sort((a, b) => {
    const av = accessor(a);
    const bv = accessor(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return av - bv;
    return String(av).localeCompare(String(bv));
  });
  return dir === "desc" ? s.reverse() : s;
}

export function matches(haystack: Array<string | null | undefined>, q: string): boolean {
  if (!q.trim()) return true;
  const needle = q.trim().toLowerCase();
  return haystack.some((h) => (h ?? "").toLowerCase().includes(needle));
}
