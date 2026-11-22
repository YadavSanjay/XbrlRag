// Lightweight client-side stash so the Answer route can render a result produced
// on the Ask page without a round-trip (Phase 0 has no server-side answer store).
import type { QueryResponse } from "./types";

const KEY = "arag:answers";

type Stash = Record<string, QueryResponse>;

function read(): Stash {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Stash;
  } catch {
    return {};
  }
}

export function saveAnswer(resp: QueryResponse): string {
  const id = resp.query_id ?? `local-${Date.now()}`;
  const stash = read();
  stash[id] = resp;
  sessionStorage.setItem(KEY, JSON.stringify(stash));
  return id;
}

export function loadAnswer(id: string): QueryResponse | null {
  return read()[id] ?? null;
}
