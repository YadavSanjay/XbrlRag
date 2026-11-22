/**
 * Accuracy spread over repeated runs of one suite — a port of
 * `src/eval/core/repeats.py::summarize_repeats` (docs/specs/repeat-spread.md), so the
 * eval console can show the noise band without a CLI run. Keep the two in step.
 *
 * Summarises N runs over the cases common to every run: mean ± sample sd, the floor
 * (cases passed every time), the ceiling (passed at least once) and the flaky cases.
 */

export interface RepeatSummary {
  n_runs: number;
  n_cases: number;
  accuracies: number[];
  mean: number;
  std: number;
  min: number;
  max: number;
  floor: number;
  ceiling: number;
  flaky_case_ids: string[];
  flip_rate: number;
  by_kind: Record<string, RepeatSummary>;
}

export function summarizeRepeats(
  runs: Array<Record<string, boolean>>,
  kinds?: Record<string, string>,
): RepeatSummary {
  if (runs.length < 2) {
    throw new Error(`need at least 2 runs to measure spread, got ${runs.length}`);
  }
  const ids = Object.keys(runs[0])
    .filter((c) => runs.every((r) => c in r))
    .sort();
  if (!ids.length) throw new Error("no case is common to every run");
  const n = ids.length;

  const accuracies = runs.map((run) => ids.filter((c) => run[c]).length / n);
  const mean = accuracies.reduce((s, a) => s + a, 0) / accuracies.length;
  const std = Math.sqrt(
    accuracies.reduce((s, a) => s + (a - mean) ** 2, 0) / (accuracies.length - 1),
  );
  const nAlways = ids.filter((c) => runs.every((run) => run[c])).length;
  const flaky = ids.filter((c) => new Set(runs.map((run) => run[c])).size > 1);

  const byKind: Record<string, RepeatSummary> = {};
  if (kinds) {
    const kindSet = [...new Set(ids.filter((c) => c in kinds).map((c) => kinds[c]))].sort();
    for (const kind of kindSet) {
      const ofKind = ids.filter((c) => kinds[c] === kind);
      byKind[kind] = summarizeRepeats(
        runs.map((run) => Object.fromEntries(ofKind.map((c) => [c, run[c]]))),
      );
    }
  }

  return {
    n_runs: runs.length,
    n_cases: n,
    accuracies,
    mean,
    std,
    min: Math.min(...accuracies),
    max: Math.max(...accuracies),
    floor: nAlways / n,
    ceiling: (nAlways + flaky.length) / n,
    flaky_case_ids: flaky,
    flip_rate: flaky.length / n,
    by_kind: byKind,
  };
}
