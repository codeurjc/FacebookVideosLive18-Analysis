#!/usr/bin/env python3
"""Emit the paper's two final-evaluation tables from `final_evaluation.py`'s output.

`tables/alg_compare_instance.tex` (tab:alg_compare_instance) and
`tables/winners_global.tex` (tab:winners_global) are hand-formatted ACM tables carrying
numbers the notebooks compute.  Like `best_elites_table.py`, this builds them instead of
transcribing them, because the failure mode here is silent: a digit typed wrong in a
23-page manuscript looks exactly like a digit typed right.

Inputs, both written by `final_evaluation.py`:
  tables/winner_costs_per_instance.csv   best A / best B / best C cost per test run
  tables/best_elite_runs.csv             per-run RPD and Rank for every configuration

Two conventions the paper uses and the raw output does not:

  * Strategy A runs that could not serve every viewer are `E` in the logs. They are
    excluded from A's mean RPD and reported as a count in parentheses -- "A is 45 % worse"
    and "A could not answer at all" are different statements.
  * The head-to-head `winners_global` table compares the two front-runner Strategy C
    configurations against *each other*, so its RPD is relative to the better of the two on
    each run, not to the whole elite set.

Usage:
    python3 final_experimentation/paper_eval_tables.py \
        --tables tables --out ../webrtc-scalability-strategies/tables
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]
ERROR = "E"

ALG_COMPARE = r"""\begin{table}
    \caption{Comparison of the best configurations of Strategies A and B for each instance size with the best configuration of \algc, reported as RPD; the number of error cases for \alga is shown in parentheses.}
        \label{tab:alg_compare_instance}
        \begin{center}
        \begin{tabular}{|c|c|c|}
            \hline
            \makecell{\textbf{Instance size}} & \makecell{\textbf{Strat. A RPD (\%) (Error cases)}} & \makecell{\textbf{Strat. B RPD (\%)}} \\
            \hline
@@ROWS@@            \hline
            Average & @@AVG_A@@ \% & @@AVG_B@@ \% \\
            \hline
        \end{tabular}
        \end{center}
\end{table}
"""

WINNERS_GLOBAL_HEAD = r"""\begin{table}
    \caption{Mean RPD and mean rank of the two best-performing elite configurations, compared against each other over all test instances, by instance size and by server capacity.}
    \label{tab:winners_global}
    \begin{center}
    {%
    \setlength{\tabcolsep}{3pt}%
    \begin{tabular}{|c|c|c|c|c|c|c|c|c|c|}
        \hline
        \makecell{\textbf{ID}} & \makecell{\textbf{Stat.}} & \makecell{\textbf{All}} & \makecell{\textbf{Small}} & \makecell{\textbf{Medium}} & \makecell{\textbf{Big}} & \makecell{$C$=50} & \makecell{$C$=150} & \makecell{$C$=650} & \makecell{$C$=1000} \\
        \hline"""

WINNERS_GLOBAL_FOOT = r"""        \hline
    \end{tabular}%
    }%
    \end{center}
\end{table}
"""


MAX_SERVERS = r"""\begin{table}[ht!]
    \caption{Max simultaneous servers needed for a sample instance (instance 30 for each instance size). Cases where \alga could not create more servers are marked as "Error".}
    \label{tab:max_simultaneous_servers}
    \begin{tabular}{|c|ccc|ccc|ccc|ccc|}
    \hline
    & \multicolumn{12}{c|}{\textbf{Server capacity}} \\
    \cline{2-13}
    	\textbf{Instance size} & \multicolumn{3}{c|}{\textbf{50}} & \multicolumn{3}{c|}{\textbf{150}} & \multicolumn{3}{c|}{\textbf{650}} & \multicolumn{3}{c|}{\textbf{1000}} \\
        \cline{2-13}
        & \textbf{A} & \textbf{B} & \textbf{C} & \textbf{A} & \textbf{B} & \textbf{C} & \textbf{A} & \textbf{B} & \textbf{C} & \textbf{A} & \textbf{B} & \textbf{C} \\
    \hline
@@ROWS@@    \hline
    \end{tabular}
\end{table}
"""


def max_servers(summaries, winners_path, stats_csv, out, instance=30):
    """Peak concurrent media servers for the winning configuration of each strategy.

    Reads the STEPS_FULL summaries rather than the evaluation logs, because peak
    concurrency is a per-step quantity the `--irace` path never records.  An infeasible
    run still leaves a truncated CSV, so the evaluation log is what decides which cells
    read "Error" -- the same rule depth_analysis.py applies.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from depth_analysis import load_summaries, drop_infeasible

    df = drop_infeasible(load_summaries(summaries), stats_csv)
    winners = json.load(open(winners_path))
    df = df[df.apply(lambda r: winners.get(r["algorithm"]) == r["config"], axis=1)]
    df = df[df.instance == instance]

    rows = ""
    for size in SIZES:
        cells = []
        for cap in CAPACITIES:
            for alg in ("A", "B", "C"):
                sel = df[(df.instance_type == size) & (df.capacity == cap)
                         & (df.algorithm == alg)]
                cells.append("Error" if sel.empty else str(int(sel.max_servers.iloc[0])))
        rows += "    " + " & ".join([size] + cells) + r" \\" + "\n"
    path = os.path.join(out, "max_simultaneous_servers.tex")
    with open(path, "w") as fh:
        fh.write(MAX_SERVERS.replace("@@ROWS@@", rows))
    print(f"wrote {path}")
    return rows


def numeric(series):
    return pd.to_numeric(series.replace(ERROR, np.nan), errors="coerce")


def alg_compare(tables, out):
    pivot = pd.read_csv(os.path.join(tables, "winner_costs_per_instance.csv"))
    for alg in ("A", "B"):
        if alg not in pivot.columns:
            sys.exit(f"{alg} column missing -- run final_evaluation.py with the A/B logs")
    num = {a: numeric(pivot[a]) for a in ("A", "B", "C")}
    rpd = {a: 100 * (num[a] - num["C"]).abs() / num["C"] for a in ("A", "B")}
    errs = (pivot["A"] == ERROR)

    rows = ""
    for size in SIZES:
        sel = pivot["Instance type"] == size
        rows += (f"            {size} & {rpd['A'][sel].mean():.2f} \\% "
                 f"({int(errs[sel].sum())}) & {rpd['B'][sel].mean():.2f} \\% \\\\\n")
    body = (ALG_COMPARE
            .replace("@@ROWS@@", rows)
            .replace("@@AVG_A@@", f"{rpd['A'].mean():.2f}")
            .replace("@@AVG_B@@", f"{rpd['B'].mean():.2f}"))
    path = os.path.join(out, "alg_compare_instance.tex")
    with open(path, "w") as fh:
        fh.write(body)
    print(f"wrote {path}")
    print(f"  A: {rpd['A'].mean():.2f}% mean RPD, {int(errs.sum())} infeasible runs")
    print(f"  B: {rpd['B'].mean():.2f}% mean RPD")
    return rpd, errs


def winners_global(tables, out, ids):
    runs = pd.read_csv(os.path.join(tables, "best_elite_runs.csv"))
    c = runs[runs["Strategy"] == "C"].copy()
    mean_rpd = c.groupby("Configuration")["RPD"].mean().sort_values()
    finalists = list(mean_rpd.index[:2])
    by_rank = c.groupby("Configuration")["Rank"].mean().idxmin()
    if by_rank not in finalists:
        finalists.append(by_rank)

    # Recompute head to head: rank and RPD relative to the better of the finalists only.
    head = c[c.Configuration.isin(finalists)].copy()
    head["cost_num"] = numeric(head["Cost"])
    group = ["Instance type", "Server capacity", "Instance"]
    head["Rank"] = head.groupby(group)["cost_num"].rank(method="min")
    best = head.groupby(group)["cost_num"].transform("min")
    head["RPD"] = 100 * (head["cost_num"] - best).abs() / best

    slices = [("All", head)]
    slices += [(s, head[head["Instance type"] == s]) for s in SIZES]
    slices += [(c_, head[head["Server capacity"] == c_]) for c_ in CAPACITIES]

    lines = []
    for cfg in finalists:
        cid = ids.get(cfg, "?")
        for stat in ("RPD", "Rank"):
            cells = []
            for _, sub in slices:
                sel = sub[sub.Configuration == cfg]
                if stat == "RPD":
                    cells.append(f"{sel.RPD.mean():.3f} ({sel.RPD.std():.3f})")
                else:
                    cells.append(f"{sel.Rank.mean():.3f}")
            label = r"RPD (\%) (SD)" if stat == "RPD" else "Rank"
            lines.append("        " + " & ".join([str(cid), label] + cells) + r" \\")
    path = os.path.join(out, "winners_global.tex")
    with open(path, "w") as fh:
        fh.write(WINNERS_GLOBAL_HEAD + "\n" + "\n".join(lines) + "\n" + WINNERS_GLOBAL_FOOT)
    print(f"wrote {path}")
    for cfg in finalists:
        sel = head[head.Configuration == cfg]
        print(f"  ID {ids.get(cfg, '?')}: overall RPD {sel.RPD.mean():.3f}, "
              f"rank {sel.Rank.mean():.3f}   {cfg}")
    return finalists


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tables", default="tables")
    ap.add_argument("--elites", default="tables/best_elites.json")
    ap.add_argument("--out", default="../webrtc-scalability-strategies/tables")
    ap.add_argument("--summaries", default="steps_full/summary")
    ap.add_argument("--winners", default="tables/winners.json")
    ap.add_argument("--stats", default="tables/best_elite_runs.csv")
    args = ap.parse_args()

    # Same ID assignment as best_elites_table.py: table order over distinct winners.
    with open(args.elites) as fh:
        elites = json.load(fh)
    cells = {}
    for label, where in elites.items():
        for size, cap in where:
            cells[(size, int(cap))] = label
    ids, n = {}, 1
    for size in SIZES:
        for cap in CAPACITIES:
            label = cells.get((size, cap))
            if label and label not in ids:
                ids[label] = n
                n += 1

    os.makedirs(args.out, exist_ok=True)
    alg_compare(args.tables, args.out)
    winners_global(args.tables, args.out, ids)
    if os.path.isdir(args.summaries):
        max_servers(args.summaries, args.winners, args.stats, args.out)
    else:
        print(f'  (no {args.summaries}; skipping max_simultaneous_servers)')
    return 0


if __name__ == "__main__":
    sys.exit(main())
