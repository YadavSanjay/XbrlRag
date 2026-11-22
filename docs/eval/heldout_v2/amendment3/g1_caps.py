"""G1/G2 expected cost and caps by question type (C4 cap rule). Read-only on xbrl_db's eval schema.

Rule: cap = 1.5 x sum over shapes (basis mean cost per case of the shape x cases of that shape),
per arm and set. The oracle's basis is its cache structure (write per reused filing, read per
later case, full price per single-use filing). Expected = the same sum without the 1.5 and, for the
tools agents, with each basis case clipped at the D6 per-case budget ($0.23).

Set composition, aggregates only:
- H-F main 390 = 3 phrasings x intents (E4 build summary): lookup 38, ratio 38, screens 26
  (7/7/6/6 over top, bottom, sum, median; the split is not recorded, so the four share one
  mean), peer 6, yoy 5, difference 5, cagr 4, trend 4, point-in-time 4. H-F compound 51.
- H-X 75 (Amendment 1.1): cross-filer 24 (4 each top, bottom, count, sum, median, peer),
  cross-period 15 (5 each trend, cagr, point-in-time), compound 18, honesty 9, text 9.
"""
import sys
from statistics import mean

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
sys.path.insert(0, ".")
from src.db.conn import connect  # noqa: E402

D6_CASE = 0.23
SCREENS = ("screen.top", "screen.bottom", "screen.sum", "screen.median")
HF = {"lookup": 114, "compute.ratio": 114, "SCREEN": 78, "peer_relative": 18,
      "compute.yoy_growth": 15, "compute.difference": 15, "compute.cagr": 12, "trend": 12,
      "point_in_time": 12}
HF_COMPOUND = 51
HX = {"screen.top": 4, "screen.bottom": 4, "SCREEN": 4, "screen.sum": 4, "screen.median": 4,
      "peer_relative": 4, "trend": 5, "compute.cagr": 5, "point_in_time": 5, "OTHER": 18}
HX_COMPOUND = 18

# Basis runs (dev, the look's code or the 11-10 baseline). See C4 for the reasons.
BASIS = {
    "xbrlrag_anthropic": "eval-89d896e3d40d",   # full dev, 595 cases
    "frontier_opus": "eval-5471b3c99d92",       # dev sample, 100 cases
    "frontier_v4pro": "eval-a99badef421c",      # dev sample, 93 cases
    "closed_book": "eval-d1e4491ef701",         # full dev, 593 cases
}
COMPOUND_DS = "eval-c78dd0934732"          # xbrlrag deepseek, dev compound 136, 6ed865a
DEV_DS = "eval-87895b6e0891"               # xbrlrag deepseek, full dev, 11-10
PAIR = ("eval-7fe5ce2c0c14", "eval-2e0653f5a8d4")  # openai-6.1 vs anthropic, same 10 cases
ORACLE_SMOKE = "eval-4f354a45a24b"
OPUS = {"input": 4e-6, "cread": 0.2e-6, "cwrite": 5e-6, "output": 20e-6}

cur = connect().cursor()


def case_costs(rid: str) -> dict[str, list[float]]:
    cur.execute("""select coalesce((select substr(t, 7) from unnest(tags) t where t like 'shape:%%'
                                    limit 1), kind), cost_usd
                     from eval.eval_case_result where run_id=%s""", (rid,))
    out: dict[str, list[float]] = {}
    for shape, c in cur.fetchall():
        out.setdefault(shape, []).append(float(c or 0))
    return out


def run_mean(rid: str) -> float:
    cur.execute("select total_cost_usd / n_cases from eval.eval_run where run_id=%s", (rid,))
    return float(cur.fetchone()[0])


def shape_means(costs: dict[str, list[float]], clip: float | None) -> dict[str, float]:
    f = (lambda x: min(x, clip)) if clip else (lambda x: x)
    m = {s: mean(f(x) for x in v) for s, v in costs.items()}
    m["SCREEN"] = mean(f(x) for s in SCREENS for x in costs.get(s, []))
    m["OTHER"] = mean(f(x) for v in costs.values() for x in v)
    return m


def total(means: dict[str, float], mix: dict[str, int]) -> float:
    return sum(means[s] * n for s, n in mix.items())


rows = []
ratio_ds = run_mean(COMPOUND_DS) / run_mean(DEV_DS)
print(f"compound/single ratio on deepseek: "
      f"{run_mean(COMPOUND_DS):.4f}/{run_mean(DEV_DS):.4f} = {ratio_ds:.2f}")
for arm, rid in BASIS.items():
    costs = case_costs(rid)
    clip = D6_CASE if arm.startswith("frontier") else None
    exp_m, cap_m = shape_means(costs, clip), shape_means(costs, None)
    if arm.startswith("frontier"):
        # Compound: a screen plus a dependent step, bounded by the D6 per-case budget.
        exp_c = min(exp_m["SCREEN"] + exp_m["compute.ratio"], D6_CASE)
        cap_c = D6_CASE
    else:
        exp_c = cap_c = run_mean(rid) * ratio_ds
    for set_name, mix, nc in (("H-F", HF, HF_COMPOUND), ("H-X", HX, HX_COMPOUND)):
        e = total(exp_m, mix) + exp_c * nc
        c = 1.5 * (total(cap_m, mix) + cap_c * nc)
        rows.append((arm, set_name, sum(mix.values()) + nc, e, c))
    if arm == "xbrlrag_anthropic":
        k = run_mean(PAIR[0]) / run_mean(PAIR[1])
        print(f"G2 scale openai-6.1/anthropic (paired 10 cases): {k:.3f}")
        for set_name, mix, nc in (("H-F", HF, HF_COMPOUND), ("H-X", HX, HX_COMPOUND)):
            e = k * (total(exp_m, mix) + exp_c * nc)
            rows.append(("G2_xbrlrag_openai61", set_name, sum(mix.values()) + nc, e, 1.5 * e))

# Oracle, from the smoke's measured tokens per call.
cur.execute("""with c as (select query_id from eval.eval_case_result where run_id=%s)
               select avg((payload->>'prompt_tokens')::bigint),
                      avg((payload->>'completion_tokens')::bigint)
                 from audit.query_event e join c using (query_id) where e.kind='llm_call'""",
            (ORACLE_SMOKE,))
p, o = (float(x) for x in cur.fetchone())
write = p * OPUS["cwrite"] + o * OPUS["output"]
read = p * OPUS["cread"] + o * OPUS["output"]
single = p * OPUS["input"] + o * OPUS["output"]
print(f"oracle per call: prompt {p:.0f} tok, output {o:.0f}; write ${write:.4f}, read ${read:.4f}, "
      f"single-use ${single:.4f}")
n_hf = 294  # lookup + ratio + yoy + difference + cagr + trend + point-in-time cases
for F, label in ((24, "F=24"), (40, "F=40"), (98, "F=98 (ceiling: one filing per intent)")):
    e = F * write + (n_hf - F) * read
    rows.append((f"oracle_opus {label}", "H-F", n_hf, e, 1.5 * e))
n_hx = 33  # cross-period 15 + honesty 9 + text 9, all single-use (bound)
rows.append(("oracle_opus (all single-use)", "H-X", n_hx, n_hx * single, 1.5 * n_hx * single))

print(f"\n{'arm':40s} {'set':4s} {'cases':>5s} {'expected':>9s} {'cap':>8s}")
for arm, s, n, e, c in rows:
    print(f"{arm:40s} {s:4s} {n:5d} ${e:8.2f} ${c:7.2f}")
