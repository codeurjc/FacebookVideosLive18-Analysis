#!/usr/bin/env python3
"""Final-evaluation tables, rebuilt from the per-size evaluation logs.

This is the part of `irace.ipynb` that reads `test_elite_configs/*.log` and emits the
paper's final-evaluation tables, lifted out of the notebook so it can be run from a shell
without R or Jupyter.  The statistics are the notebook's:

  Rank   rank of a configuration's cost within its (instance size, capacity, instance)
         group, ties taking the minimum rank
  RPD    100 * |cost - best cost| / best cost within the same group

both then averaged across groups.  Infeasible runs -- Strategy A exhausting its one
level of viewer servers -- are carried as the string "E" and excluded from the means,
but counted, because "A is 45% worse" and "A could not answer at all" are different
statements and the paper reports them side by side.

Unlike the notebook this does not hardcode which configuration won: the winner of each
strategy is the one with the lowest mean RPD, recomputed from the data.

Usage:
    python3 final_experimentation/final_evaluation.py \
        --logs test_elite_configs --out tables
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]
ERROR = "E"

HEADER_RE = re.compile(r"CAPACITY=(\d+),\s*instance=(\d+),\s*args=(.+?)\s*$")


def parse_log(path, instance_type):
    """A log alternates a header line with either "<cost> <seconds>" or an error line."""
    rows = []
    pending = None
    with open(path) as fh:
        for line in fh:
            m = HEADER_RE.search(line) if "Output for" in line else None
            if m:
                capacity, instance, config = m.groups()
                pending = {
                    "Instance type": instance_type,
                    "Server capacity": int(capacity),
                    # test instance n is reported as (n-29)t everywhere downstream
                    "Instance": f"{int(instance) - 29}t",
                    "Configuration": config,
                }
            elif pending is not None:
                pending["Cost"] = ERROR if "Error" in line else float(line.split()[0])
                rows.append(pending)
                pending = None
    return rows


def load_logs(log_dir):
    """`<size>.log` is the Strategy C / all-elites run, `<letter>_<size>.log` the rest."""
    rows = []
    found = {}
    for size in SIZES:
        for prefix, alg in (("", "C"), ("a_", "A"), ("b_", "B")):
            path = os.path.join(log_dir, f"{prefix}{size}.log")
            if not os.path.exists(path):
                continue
            new = parse_log(path, size)
            for r in new:
                r["Strategy"] = alg
            rows.extend(new)
            found.setdefault(alg, []).append(size)
    if not rows:
        sys.exit(f"no evaluation logs found in {log_dir}")
    for alg, sizes in sorted(found.items()):
        print(f"  strategy {alg}: {', '.join(sizes)}", file=sys.stderr)
    return pd.DataFrame(rows)


def numeric_cost(df):
    return pd.to_numeric(df["Cost"].replace(ERROR, np.nan), errors="coerce")


def add_rank_and_rpd(df):
    """Rank and RPD within each (instance size, capacity, instance) group."""
    df = df.copy()
    df["cost_num"] = numeric_cost(df)
    group = ["Instance type", "Server capacity", "Instance"]
    df["Rank"] = df.groupby(group)["cost_num"].rank(method="min")
    best = df.groupby(group)["cost_num"].transform("min")
    df["RPD"] = (100 * (df["cost_num"] - best).abs() / best).round(3)
    return df


def group_stats(stats, by):
    """Mean RPD with its SD, plus mean rank -- the notebook's `group_alt_results`."""
    g = stats.groupby(by)
    out = g["RPD"].mean().round(3).reset_index().rename(columns={"RPD": "mean"})
    out["sd"] = g["RPD"].std().round(3).reset_index()["RPD"]
    out["Mean RPD (SD)"] = out["mean"].astype(str) + " (" + out["sd"].astype(str) + ")"
    out["Mean Rank"] = g["Rank"].mean().round(3).reset_index()["Rank"]
    out["Errors"] = g["Cost"].apply(lambda s: (s == ERROR).sum()).reset_index()["Cost"]
    return out.drop(columns=["mean", "sd"]).sort_values(by)


def to_latex(df, path, caption=None, label=None, float_format="%.3f"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(df.to_latex(
            index=False, float_format=float_format, escape=False,
            column_format="l" + "c" * (len(df.columns) - 1),
            caption=caption, label=label))
    print(f"  wrote {path}")


def winners_per_strategy(stats):
    """Lowest mean RPD across every group, computed per strategy."""
    winners = {}
    for alg, sub in stats.groupby("Strategy"):
        per_config = sub.groupby("Configuration")["RPD"].mean()
        # a strategy that errors everywhere for some config still ranks on what it solved
        winners[alg] = per_config.idxmin()
    return winners


def winners_global(stats, out):
    """RPD and rank of the two front-runner Strategy C configurations, sliced every way.

    The manuscript's `winners_global` table: the configuration with the lowest mean RPD and
    the one with the lowest mean rank, recomputed head-to-head (so RPD is relative to the
    better of the two, not to the whole elite set) across all runs, each instance size and
    each capacity.
    """
    c = stats[stats.Strategy == "C"]
    mean_rpd = c.groupby("Configuration")["RPD"].mean().sort_values()
    by_rank = c.groupby("Configuration")["Rank"].mean().idxmin()
    # the two cheapest, plus the rank winner when it is neither of them
    finalists = list(mean_rpd.index[:2])
    if by_rank not in finalists:
        finalists.append(by_rank)
    head = add_rank_and_rpd(c[c.Configuration.isin(finalists)])

    slices = [("All", head)]
    slices += [(sz, head[head["Instance type"] == sz]) for sz in SIZES]
    slices += [(f"C={cap}", head[head["Server capacity"] == cap]) for cap in CAPACITIES]

    rows = []
    for cfg in finalists:
        rpd_row = {"Configuration": cfg, "Stat": "RPD (\%) (SD)"}
        rank_row = {"Configuration": cfg, "Stat": "Rank"}
        for name, sub in slices:
            sel = sub[sub.Configuration == cfg]
            rpd_row[name] = f"{sel.RPD.mean():.3f} ({sel.RPD.std():.3f})"
            rank_row[name] = round(sel.Rank.mean(), 3)
        rows += [rpd_row, rank_row]
    df = pd.DataFrame(rows)
    to_latex(df, f"{out}/winners_global.tex",
             caption="Mean RPD and mean rank for the winning configurations.",
             label="tab:winners_global")
    return df


def comparison_table(df, winners):
    """Best A and best B as RPD against best C, per instance -- the paper's Table 9."""
    chosen = df[df.apply(lambda r: winners.get(r["Strategy"]) == r["Configuration"], axis=1)]
    pivot = chosen.pivot_table(
        index=["Instance type", "Server capacity", "Instance"],
        columns="Strategy", values="Cost", aggfunc="first").reset_index()
    for alg in ("A", "B"):
        if alg not in pivot.columns:
            pivot[alg] = np.nan
    num = {a: pd.to_numeric(pivot[a].replace(ERROR, np.nan), errors="coerce")
           for a in ("A", "B", "C")}
    for alg in ("A", "B"):
        pivot[f"{alg} RPD"] = (100 * (num[alg] - num["C"]).abs() / num["C"])
        pivot[f"{alg} errors"] = (pivot[alg] == ERROR).astype(int)
    return pivot


def summarise_comparison(pivot, by):
    g = pivot.groupby(by)
    out = pd.DataFrame({
        "Strat. A RPD (\\%)": g["A RPD"].mean().round(2),
        "Strat. A errors": g["A errors"].sum(),
        "Strat. B RPD (\\%)": g["B RPD"].mean().round(2),
        "Strat. B errors": g["B errors"].sum(),
    }).reset_index()
    overall = pd.DataFrame([{
        by[0] if len(by) == 1 else by[0]: "Average",
        "Strat. A RPD (\\%)": round(pivot["A RPD"].mean(), 2),
        "Strat. A errors": int(pivot["A errors"].sum()),
        "Strat. B RPD (\\%)": round(pivot["B RPD"].mean(), 2),
        "Strat. B errors": int(pivot["B errors"].sum()),
    }])
    return pd.concat([out, overall], ignore_index=True)


# Cost model: USD/hour for a capacity-50 server, scaled by capacity; the "exponential"
# variant charges a further 1.25x for the larger machines.  Objectives are server-seconds.
BASE_COST_PER_SECOND = 0.005 / 3600
LINEAR_MULT = {50: 1, 150: 3, 650: 13, 1000: 20}
EXP_MULT = {50: 1, 150: 3 * 1.25, 650: 13 * 1.25, 1000: 20 * 1.25}


def cost_table(pivot, out):
    """Estimated USD cost of the winning Strategy C configuration.

    The paper reports a single test instance (1t, i.e. instance 30) rather than an average,
    so both are given: that figure and the mean over the ten test instances, which is the
    more robust number.
    """
    rows = []
    for size in SIZES:
        for cap in CAPACITIES:
            sel = pivot[(pivot["Instance type"] == size) & (pivot["Server capacity"] == cap)]
            if sel.empty:
                continue
            cost_c = pd.to_numeric(sel["C"].replace(ERROR, np.nan), errors="coerce")
            one = cost_c[sel["Instance"] == "1t"]
            row = {"Instance type": size, "Server capacity": cap}
            for name, mult in (("Lineal", LINEAR_MULT), ("Exponential", EXP_MULT)):
                unit = BASE_COST_PER_SECOND * 3600 * mult[cap]
                # kept as a string: the unit price needs five decimals while the totals
                # need two, and to_latex applies one float format to the whole table
                row[f"{name} unit (USD/h)"] = f"{unit:.5f}"
                row[f"{name} total (1t)"] = (
                    round(float(one.iloc[0] * BASE_COST_PER_SECOND * mult[cap]), 2)
                    if len(one) and pd.notna(one.iloc[0]) else "--")
                row[f"{name} mean (10 inst.)"] = round(
                    float(cost_c.mean() * BASE_COST_PER_SECOND * mult[cap]), 2)
            rows.append(row)
    df = pd.DataFrame(rows)
    to_latex(df, f"{out}/costs_all.tex",
             caption=("Estimated total cost of the winning Strategy C configuration under "
                      "the lineal and exponential pricing models, for the first test "
                      "instance and averaged over all ten."),
             label="tab:costs_all", float_format="%.2f")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="test_elite_configs")
    ap.add_argument("--out", default="tables")
    args = ap.parse_args()

    print(f"reading logs from {args.logs}", file=sys.stderr)
    df = load_logs(args.logs)
    stats = add_rank_and_rpd(df)
    os.makedirs(args.out, exist_ok=True)

    # Per-strategy elite comparisons, the notebook's alt_results* family.
    for alg, sub in stats.groupby("Strategy"):
        suffix = "" if alg == "C" else f"_{alg.lower()}"
        to_latex(group_stats(sub, ["Configuration"]),
                 f"{args.out}/alt_results_all{suffix}.tex")
        to_latex(group_stats(sub, ["Instance type", "Configuration"]),
                 f"{args.out}/alt_results_instance{suffix}.tex")
        if alg == "C":
            to_latex(group_stats(sub, ["Server capacity", "Configuration"]),
                     f"{args.out}/alt_results_server.tex")
            to_latex(group_stats(sub, ["Instance type", "Server capacity", "Configuration"]),
                     f"{args.out}/alt_results.tex")

    winners_global(stats, args.out)

    winners = winners_per_strategy(stats)
    print("\nwinning configuration per strategy (lowest mean RPD):", file=sys.stderr)
    for alg in sorted(winners):
        print(f"  {alg}: {winners[alg]}", file=sys.stderr)

    pivot = comparison_table(df, winners)
    pivot.to_csv(f"{args.out}/winner_costs_per_instance.csv", index=False)

    if pivot["A RPD"].notna().any() or pivot["B RPD"].notna().any():
        to_latex(summarise_comparison(pivot, ["Instance type"]),
                 f"{args.out}/alg_comparison_instance.tex")
        to_latex(summarise_comparison(pivot, ["Server capacity"]),
                 f"{args.out}/alg_comparison_server.tex")
    else:
        print("  (no A/B logs -- skipping the strategy comparison tables)", file=sys.stderr)

    cost_table(pivot, args.out)

    stats.drop(columns=["cost_num"]).to_csv(f"{args.out}/best_elite_runs.csv", index=False)
    with open(f"{args.out}/winners.json", "w") as fh:
        json.dump(winners, fh, indent=1)
    print(f"\ndone -- {len(df)} runs across {df['Strategy'].nunique()} strategies", file=sys.stderr)


if __name__ == "__main__":
    main()
