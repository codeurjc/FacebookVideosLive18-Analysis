#!/usr/bin/env python3
"""Interconnection depth of the session trees, as a latency proxy.

`max_tree_depth` and `avg_tree_depth` count **media server levels** on the live session
trees at each simulation step: a session served by a single media server has depth 1, and
a viewer sitting on the deepest level is `depth - 1` server hops away from its publisher.
Depth is simulator state derived from the per-session parent pointers, so it is directly
comparable across strategies.

What this reports, and what it deliberately does not:

  * the depth a strategy actually reaches, and how long it holds it (time- and
    viewer-time-weighted, so a spike lasting a second does not read like a steady state);
  * the cost of *not* going deep -- the RPD penalty for picking the shallowest elite
    configuration instead of the cheapest one.

It does **not** convert depth into milliseconds.  The companion testbed
(mediasoup-LLLS-experiments) measured chains of up to 20 real media servers and found no
RTT increase over a single server -- the per-hop cost sits below that measurement's noise
floor -- so there is no per-hop figure to multiply by, and depths beyond 20 are outside
what has been validated on real servers.  That is the point the tables are here to make.

Usage:
    python3 final_experimentation/depth_analysis.py \
        --summaries steps_full/summary --winners tables/winners.json --out tables
"""
import argparse
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]

# Okabe-Ito blue / vermillion / bluish-green: passes the six-check palette validator
# (lightness band, chroma floor, CVD separation, normal-vision floor, contrast).
STRATEGY_COLOR = {"A": "#0072B2", "B": "#D55E00", "C": "#009E73"}
# Capacity is a magnitude, so it gets one hue light->dark rather than four hues.
CAPACITY_COLOR = {50: "#08306b", 150: "#2171b5", 650: "#6baed6", 1000: "#c6dbef"}

# Paper figure geometry (matches simulated.ipynb / the acmart column width).
FIG_W, FIG_H, DPI = 3.35, 3.35 * 0.62, 400
plt.rcParams.update({
    "font.size": 6, "axes.labelsize": 6, "axes.titlesize": 7,
    "xtick.labelsize": 5.5, "ytick.labelsize": 5.5, "legend.fontsize": 5.5,
    "lines.linewidth": 1.0, "figure.dpi": DPI,
})


def load_summaries(path):
    """Every `<archive>.jsonl` in the directory, not just the Strategy C ones.

    `reduce_archives.sh` names each summary after its archive, so the C grid lands in
    `{small,medium,big}.jsonl` and the per-strategy runs in `a_<size>.jsonl` /
    `b_<size>.jsonl`.  Reading only `<size>.jsonl` silently produced C-only tables even
    once the A and B archives were reduced, so read them all -- each record carries its
    own `algorithm`, so the merge is unambiguous.
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    df = pd.DataFrame(rows)
    df["instance_type"] = pd.Categorical(df["instance_type"], SIZES, ordered=True)
    return df


def to_latex(df, path, caption=None, label=None, float_format="%.1f"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(df.to_latex(index=False, escape=False, float_format=float_format,
                             caption=caption, label=label,
                             column_format="l" + "c" * (len(df.columns) - 1)))
    print(f"  wrote {path}")


def weighted_percentile(hist, q):
    """Percentile of depth by the time that depth was held.

    `depth_time_hist` maps each observed depth to the number of simulated seconds the
    session trees stood at it, so this is an exact time-weighted percentile over the run,
    not a sample of one -- which is what R2-C1 asked for when it wanted p95/p99.
    """
    if not hist:
        return float("nan")
    items = sorted((float(k), v) for k, v in hist.items())
    total = sum(v for _, v in items)
    if total <= 0:
        return float("nan")
    target, acc = q * total, 0.0
    for depth, w in items:
        acc += w
        if acc >= target:
            return depth
    return items[-1][0]


def add_percentiles(df):
    for q, name in ((0.5, "p50"), (0.95, "p95"), (0.99, "p99")):
        df[f"depth_{name}"] = df["depth_time_hist"].apply(
            lambda h, q=q: weighted_percentile(h, q))
    return df


def drop_infeasible(df, stats_csv):
    """Remove runs the evaluation log reports as errors.

    A strategy that cannot serve every viewer exits mid-simulation, and `StepSaver` has
    already flushed whole buffers by then -- so the archive still holds a CSV, truncated at
    a multiple of `buffer.size` (10,000 rows). Reduced naively those look like short, shallow
    runs and would drag every average down. The per-size evaluation log is authoritative
    about which (size, capacity, instance, configuration) failed, so use it.
    """
    before = len(df)
    df = df[df.n_rows > 0]
    if os.path.exists(stats_csv):
        stats = pd.read_csv(stats_csv)
        bad = {
            (r["Instance type"], int(r["Server capacity"]),
             int(str(r["Instance"]).rstrip("t")) + 29, r["Configuration"])
            for _, r in stats[stats.Cost.astype(str) == "E"].iterrows()
        }
        if bad:
            key = list(zip(df.instance_type.astype(str), df.capacity,
                           df.instance, df.config))
            df = df[[k not in bad for k in key]]
    dropped = before - len(df)
    if dropped:
        print(f"  dropped {dropped} infeasible/truncated runs")
    return df


def winner_rows(df, winners):
    """The runs of each strategy's cost-winning configuration."""
    keep = df.apply(lambda r: winners.get(r["algorithm"]) == r["config"], axis=1)
    return df[keep]


def table_depth_by_strategy(win, out):
    """Max depth reached, per instance size x capacity x strategy (mean over the 10
    test instances) -- laid out like the paper's max_simultaneous_servers table."""
    rows = []
    for size in SIZES:
        row = {"Instance size": size}
        for cap in CAPACITIES:
            for alg in ("A", "B", "C"):
                sel = win[(win.instance_type == size) & (win.capacity == cap)
                          & (win.algorithm == alg)]
                row[f"{cap}/{alg}"] = round(sel.max_tree_depth.mean(), 1) if len(sel) else "--"
        rows.append(row)
    df = pd.DataFrame(rows)
    to_latex(df, f"{out}/tree_depth_by_strategy.tex",
             caption=("Maximum interconnection depth (media server levels) reached by the "
                      "cost-winning configuration of each strategy, averaged over the ten "
                      "test instances. A viewer on the deepest level is $depth-1$ server "
                      "hops from its publisher."),
             label="tab:tree_depth_by_strategy")
    return df


def table_max_servers(win, out, instance=30):
    """Peak concurrent media servers -- the manuscript's max_simultaneous_servers table.

    Reported for a single sample instance, as the manuscript does, because it is a
    provisioning figure rather than an average: it is what the deployment must be able to
    stand up at once.  A strategy that cannot serve every viewer shows "Error".
    """
    # An infeasible run writes no CSV, so it is simply absent from the summaries -- but so
    # is a strategy that was never run.  Only call a gap "Error" if that strategy produced
    # data somewhere; otherwise it was not run and the cell is "--".
    present = set(win.algorithm.unique())
    rows = []
    for size in SIZES:
        row = {"Instance size": size}
        for cap in CAPACITIES:
            for alg in ("A", "B", "C"):
                sel = win[(win.instance_type == size) & (win.capacity == cap)
                          & (win.algorithm == alg) & (win.instance == instance)]
                if len(sel):
                    row[f"{cap}/{alg}"] = int(sel.iloc[0].max_servers)
                else:
                    row[f"{cap}/{alg}"] = "Error" if alg in present else "--"
        rows.append(row)
    df = pd.DataFrame(rows)
    to_latex(df, f"{out}/max_simultaneous_servers.tex",
             caption=(f"Maximum simultaneous media servers needed for a sample instance "
                      f"(instance {instance} of each size), for the cost-winning "
                      f"configuration of each strategy. Cases where a strategy could not "
                      f"serve every viewer are marked Error."),
             label="tab:max_simultaneous_servers", float_format="%.0f")
    return df


def table_depth_weighted(win, out):
    """Depth weighted by how long it is held, and by how many viewers hold it."""
    rows = []
    for size in SIZES:
        for cap in CAPACITIES:
            for alg in ("A", "B", "C"):
                sel = win[(win.instance_type == size) & (win.capacity == cap)
                          & (win.algorithm == alg)]
                if not len(sel):
                    continue
                rows.append({
                    "Instance size": size, "Capacity": cap, "Strategy": alg,
                    "Max depth": round(sel.max_tree_depth.mean(), 1),
                    "Time-wt. mean": round(sel.tw_mean_max_depth.mean(), 2),
                    "Viewer-wt. mean": round(sel.vw_mean_max_depth.mean(), 2),
                    "p50": round(sel.depth_p50.mean(), 1),
                    "p95": round(sel.depth_p95.mean(), 1),
                    "p99": round(sel.depth_p99.mean(), 1),
                })
    df = pd.DataFrame(rows)
    to_latex(df, f"{out}/tree_depth_weighted.tex",
             caption=("Interconnection depth of the cost-winning configuration: the maximum "
                      "reached, the mean weighted by how long each depth is held and by how "
                      "many viewers are connected while it is held, and time-weighted "
                      "percentiles of the depth distribution."),
             label="tab:tree_depth_weighted", float_format="%.2f")
    return df


def table_depth_vs_cost(df, stats_csv, out):
    """Across Strategy C's elite configurations: what depth does each cost level buy?"""
    if not os.path.exists(stats_csv):
        print(f"  (no {stats_csv}; skipping depth-vs-cost table)")
        return None
    stats = pd.read_csv(stats_csv)
    rpd = stats[stats.Strategy == "C"].groupby("Configuration")["RPD"].mean()
    c = df[df.algorithm == "C"]
    agg = c.groupby("config").agg(
        max_depth=("max_tree_depth", "mean"),
        vw_depth=("vw_mean_max_depth", "mean"),
        max_servers=("max_servers", "mean")).reset_index()
    agg["Mean RPD (\\%)"] = agg["config"].map(rpd).round(3)
    agg = agg.sort_values("Mean RPD (\\%)")
    agg = agg.rename(columns={"config": "Configuration", "max_depth": "Mean max depth",
                              "vw_depth": "Viewer-wt. depth",
                              "max_servers": "Mean max servers"})
    agg = agg[["Configuration", "Mean RPD (\\%)", "Mean max depth",
               "Viewer-wt. depth", "Mean max servers"]]
    to_latex(agg.round(2), f"{out}/depth_vs_cost.tex",
             caption=("Strategy C elite configurations ordered by cost (mean RPD), with "
                      "the interconnection depth each reaches, averaged over all instance "
                      "sizes and server capacities. Depth is not monotone in cost: the two "
                      "cheapest configurations sit mid-range, the deepest two are the third "
                      "and fourth cheapest, and the shallowest costs about 3.3 percentage "
                      "points more RPD than the cheapest."),
             label="tab:depth_vs_cost", float_format="%.2f")
    return agg


def table_tradeoff(df, out):
    """Per group: depth of the cheapest configuration, shallowest depth available, and
    what choosing the shallowest one costs."""
    rows = []
    for (size, cap), sub in df[df.algorithm == "C"].groupby(
            ["instance_type", "capacity"], observed=True):
        pen, d_cost, d_min = [], [], []
        for inst, g in sub.groupby("instance"):
            cheap = g.loc[g.objective.idxmin()]
            shallow = g.loc[g.max_tree_depth.idxmin()]
            d_cost.append(cheap.max_tree_depth)
            d_min.append(shallow.max_tree_depth)
            pen.append(100 * (shallow.objective - cheap.objective) / cheap.objective)
        rows.append({
            "Instance size": size, "Capacity": cap,
            "Depth at min cost": round(np.mean(d_cost), 1),
            "Min available depth": round(np.mean(d_min), 1),
            "Cost of going shallow (\\%)": round(np.mean(pen), 2),
        })
    tdf = pd.DataFrame(rows)
    to_latex(tdf, f"{out}/depth_cost_tradeoff.tex",
             caption=("Cost of bounding the interconnection depth: the depth reached by the "
                      "cheapest elite configuration, the shallowest depth any elite "
                      "configuration achieves, and the objective penalty for choosing it."),
             label="tab:depth_cost_tradeoff", float_format="%.2f")
    return tdf


def plot_depth_vs_capacity(win, out):
    """Depth against capacity, one line per instance size, per strategy."""
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    markers = {"small": "o", "medium": "s", "big": "^"}
    for alg in ("A", "B", "C"):
        for size in SIZES:
            sel = win[(win.algorithm == alg) & (win.instance_type == size)]
            if not len(sel):
                continue
            m = sel.groupby("capacity").max_tree_depth.mean().reindex(CAPACITIES)
            ax.plot(CAPACITIES, m.values, marker=markers[size], markersize=3,
                    color=STRATEGY_COLOR[alg], linestyle="-",
                    label=f"{alg} / {size}")
    ax.axhspan(1, 20, color="#999999", alpha=0.18, lw=0)
    ax.text(52, 1.15, "depth validated on real media servers ($\\leq$ 20 hops)",
            fontsize=4.6, color="#555555", va="bottom", ha="left")
    ax.set_yscale("log")
    ax.set_xscale("log")
    ax.set_xticks(CAPACITIES)
    ax.set_xticklabels([str(c) for c in CAPACITIES])
    ax.set_xlabel("Media server capacity $C$")
    ax.set_ylabel("Max interconnection depth (levels)")
    ax.grid(True, which="both", lw=0.3, alpha=0.4)
    ax.legend(ncol=3, frameon=False, columnspacing=0.8, handlelength=1.4)
    fig.tight_layout(pad=0.3)
    p = f"{out}/plots/depth_vs_capacity.png"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p}")


def plot_depth_cost_pareto(df, stats_csv, out):
    """Each elite C configuration as a (cost, depth) point -- the trade-off, per capacity."""
    if not os.path.exists(stats_csv):
        return
    stats = pd.read_csv(stats_csv)
    c = df[df.algorithm == "C"]
    fig, axes = plt.subplots(1, 4, figsize=(FIG_W * 2.1, FIG_H), sharey=True)
    for ax, cap in zip(axes, CAPACITIES):
        sub = c[c.capacity == cap]
        st = stats[(stats.Strategy == "C") & (stats["Server capacity"] == cap)]
        rpd = st.groupby("Configuration")["RPD"].mean()
        agg = sub.groupby("config").max_tree_depth.mean()
        for cfg in agg.index:
            if cfg not in rpd:
                continue
            ses = "RT=SES" in cfg
            ax.scatter(rpd[cfg], agg[cfg], s=14 if ses else 9,
                       color="#D55E00" if ses else "#0072B2",
                       marker="D" if ses else "o", zorder=3,
                       label="$OCR$=SES" if ses else "$OCR$=SRV")
        ax.set_yscale("log")
        ax.set_title(f"$C$={cap}")
        ax.set_xlabel("Mean RPD (%)")
        ax.grid(True, which="both", lw=0.3, alpha=0.4)
    axes[0].set_ylabel("Max depth (levels)")
    h, l = axes[0].get_legend_handles_labels()
    seen = dict(zip(l, h))
    axes[0].legend(seen.values(), seen.keys(), frameon=False, loc="upper right")
    fig.tight_layout(pad=0.3)
    p = f"{out}/plots/depth_cost_pareto.png"
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summaries", default="steps_full/summary")
    ap.add_argument("--winners", default="tables/winners.json")
    ap.add_argument("--stats", default="tables/best_elite_runs.csv")
    ap.add_argument("--out", default="tables")
    args = ap.parse_args()

    df = add_percentiles(load_summaries(args.summaries))
    print(f"loaded {len(df)} runs: {sorted(df.algorithm.unique())}")
    df = drop_infeasible(df, args.stats)
    winners = json.load(open(args.winners)) if os.path.exists(args.winners) else {}
    if not winners:
        # fall back to the cheapest configuration per strategy
        winners = {a: s.groupby("config").objective.mean().idxmin()
                   for a, s in df.groupby("algorithm")}
    win = winner_rows(df, winners)

    table_max_servers(win, args.out)
    table_depth_by_strategy(win, args.out)
    table_depth_weighted(win, args.out)
    table_depth_vs_cost(df, args.stats, args.out)
    table_tradeoff(df, args.out)
    plot_depth_vs_capacity(win, args.out)
    plot_depth_cost_pareto(df, args.stats, args.out)


if __name__ == "__main__":
    main()
