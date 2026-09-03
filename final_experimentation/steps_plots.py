#!/usr/bin/env python3
"""Per-step figures, rebuilt from the decimated STEPS_FULL series.

`reduce_archive.py` writes one decimated series per run -- the same row-index sampling
`simulated.ipynb` did with `sample_rate=100`, but with the stride picked per run so every
series lands at 20k-40k rows.  That is 800 MB for the whole grid instead of 457 GB, so
the figures below can be regenerated on a laptop.

Two figures per (instance size, capacity):

  servers_in_use   viewers offered, media servers in use, and interconnection depth,
                   as three panels on a shared time axis, one line per strategy
  depth_over_time  maximum and mean interconnection depth over the session's lifetime

The panels are stacked on a shared x-axis rather than drawn as one plot with two y-scales.
The quantities differ by three orders of magnitude, and a twin-axis plot lets the reader
read a crossing point that does not exist.

Usage:
    python3 final_experimentation/steps_plots.py \
        --series steps_full/series --summaries steps_full/summary \
        --winners tables/winners.json --out tables --instance 30
"""
import argparse
import glob
import gzip
import math
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Both scripts live in this directory, so it is already on sys.path.
from depth_analysis import drop_infeasible

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]
STRATEGY_COLOR = {"A": "#0072B2", "B": "#D55E00", "C": "#009E73"}

FIG_W, DPI = 3.35, 400
# Sized for a figure placed at about one column width. The PNG is scaled to a fixed
# fraction of \textwidth, so what matters is point size relative to FIG_W, not the pixel
# dimensions: raising these makes the labels bigger on the printed page.
plt.rcParams.update({
    "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "lines.linewidth": 0.9, "figure.dpi": DPI,
})


def load_summaries(path):
    """Every `<archive>.jsonl`, so the A and B runs are plotted alongside C.

    See the note in depth_analysis.load_summaries: reading only `<size>.jsonl` misses the
    `a_<size>` / `b_<size>` archives and yields a C-only figure.
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    return pd.DataFrame(rows)


def series_path(series_dir, size, member):
    """Locate a run's decimated series, whichever archive it was reduced from.

    `reduce_archives.sh` puts each archive's series under a directory named after the
    archive, so the Strategy C grid lands in `series/<size>/` while the per-strategy runs
    land in `series/a_<size>/` and `series/b_<size>/`.  Looking only in `series/<size>/`
    silently produced a C-only figure once A and B were reduced.
    """
    name = member.replace(".csv.zst", ".series.csv.gz")
    direct = os.path.join(series_dir, size, name)
    if os.path.exists(direct):
        return direct
    for alt in sorted(glob.glob(os.path.join(series_dir, f"*_{size}", name))):
        return alt
    return direct


def read_series(path):
    with gzip.open(path, "rt") as fh:
        return pd.read_csv(fh)


def collect(df, series_dir, size, capacity, instance, winners, start=None, end=None):
    """The winning configuration of each strategy for one (size, capacity, instance).

    `start`/`end` are seconds of simulated time; the manuscript's figure windows hours 2-6
    (7200-21600) so the individual steps stay legible.
    """
    out = {}
    for alg, cfg in winners.items():
        sel = df[(df.instance_type == size) & (df.capacity == capacity)
                 & (df.instance == instance) & (df.algorithm == alg) & (df.config == cfg)]
        if sel.empty:
            continue
        p = series_path(series_dir, size, sel.iloc[0]["member"])
        if os.path.exists(p):
            s = read_series(p)
            if start is not None:
                s = s[s["timestamp"] >= start]
            if end is not None:
                s = s[s["timestamp"] <= end]
            if len(s):
                out[alg] = s
    return out


def figure_servers_in_use(data, size, capacity, instance, out, suffix="", depth=True):
    """Viewers offered and media servers in use, optionally with interconnection depth.

    The manuscript's Figure 4 is the two-panel form -- its caption and \\Description
    describe viewers and servers only. `depth=False` reproduces exactly that; the default
    adds the depth panel, which is the same latency proxy Table 8 reports and costs no
    extra simulation.
    """
    if not data:
        return
    panels = [
        ("viewers", "Viewers connected"),
        ("n_servers_current", "Media servers in use"),
    ]
    if depth:
        panels.append(("max_tree_depth", "Max depth (levels)"))
    fig, axes = plt.subplots(len(panels), 1,
                             figsize=(FIG_W, FIG_W * (1.15 if depth else 0.85)),
                             sharex=True)
    for ax, (col, ylabel) in zip(axes, panels):
        for alg, s in sorted(data.items()):
            ax.plot(s["timestamp"] / 3600.0, s[col], color=STRATEGY_COLOR[alg],
                    label=f"Strategy {alg}")
        ax.set_ylabel(ylabel)
        ax.grid(True, lw=0.3, alpha=0.4)
    # Log only when the series actually spans orders of magnitude; on a run that stays
    # between 3 and 6 levels a log axis just produces "6 x 10^0" ticks.
    if depth:
        depth_max = max(s["max_tree_depth"].max() for s in data.values())
        if depth_max >= 40:
            axes[2].set_yscale("log")
        else:
            axes[2].yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    axes[-1].set_xlabel("Time (hours)")
    axes[0].legend(ncol=len(data), frameon=False, loc="upper right",
                   columnspacing=0.9, handlelength=1.4)
    axes[0].set_title(f"{size} instance {instance}, $C$={capacity}")
    fig.tight_layout(pad=0.3)
    p = f"{out}/plots/servers_in_use_{size}_{capacity}{suffix}.png"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p}")


# Colour for the viewer demand curve on the combined figure.  It has to read as "not a
# strategy", so it sits outside STRATEGY_COLOR and is drawn dashed.
VIEWERS_COLOR = "#7B3294"


def _sci_tick(value, _pos):
    """Format a tick as "5.0e4" / "1.0e5", the way the manuscript's figure labels them."""
    if value == 0:
        return "0"
    exponent = int(math.floor(math.log10(abs(value))))
    return f"{value / 10 ** exponent:.1f}e{exponent}"


def figure_servers_combined(data, size, capacity, instance, out, suffix=""):
    """Servers in use and viewer demand on one axes, as the manuscript's Figure 4.

    Servers go on the left axis and viewers on a twin right axis.  The two quantities
    differ by three orders of magnitude, so a twin axis invites the reader to see a
    crossing point that does not exist -- but the figure is a subfloat in a paper with a
    hard page limit, and stacking the panels costs roughly twice the height for a
    comparison the caption already makes in words.  The viewer curve is dashed and in a
    colour no strategy uses, so it does not read as a fourth strategy.

    `figure_servers_in_use` keeps the stacked-panel form, which is the better one to read
    off screen and the only one that can carry the depth panel.
    """
    if not data:
        return
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_W * 0.46))
    for alg, s in sorted(data.items()):
        ax.plot(s["timestamp"] / 3600.0, s["n_servers_current"],
                color=STRATEGY_COLOR[alg], label=f"Strategy {alg}")
    ax.set_ylabel("Servers in use")
    ax.set_xlabel("Time (hours)")
    ax.grid(True, lw=0.3, alpha=0.4)

    # Viewer demand is a property of the instance, not of the strategy: every series
    # carries the same curve, so draw it once.
    ref = data[sorted(data)[0]]
    ax2 = ax.twinx()
    ax2.plot(ref["timestamp"] / 3600.0, ref["viewers"], color=VIEWERS_COLOR,
             linestyle="--", label="# Viewers")
    ax2.set_ylabel("# Viewers")
    # Big and medium instances run to six figures, and "140000" repeated down the right
    # margin costs more width than the curve does. Each tick carries its own exponent
    # instead -- "5.0e4", "1.0e5" -- which is how the manuscript's figure reads; an
    # offset "x10^5" above the axis would be narrower still, but it puts the magnitude
    # somewhere the reader has to go and find. Small instances peak in the thousands,
    # where plain numbers are both shorter and easier to read.
    if float(ref["viewers"].max()) >= 1e4:
        ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_sci_tick))

    handles = ax.get_lines() + ax2.get_lines()
    # Framed, as the manuscript's figure has it: the legend sits above the axes on white,
    # and without a frame the entries read as floating text rather than a key.
    ax.legend(handles, [h.get_label() for h in handles], ncol=2, frameon=True,
              loc="lower center", bbox_to_anchor=(0.5, 1.0),
              columnspacing=0.9, handlelength=1.6,
              borderpad=0.3, borderaxespad=0.1, labelspacing=0.3,
              edgecolor="0.7", framealpha=1.0)
    # No padding anywhere: the figure is placed at a fixed \textwidth fraction in a
    # two-column layout, so any whitespace the PNG carries is whitespace the page loses.
    fig.tight_layout(pad=0)
    p = f"{out}/plots/servers_in_use_{size}_{capacity}{suffix}.png"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fig.savefig(p, dpi=DPI, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    print(f"  wrote {p}")


def figure_depth_over_time(data, size, capacity, instance, out, suffix=""):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_W * 0.55))
    for alg, s in sorted(data.items()):
        t = s["timestamp"] / 3600.0
        ax.plot(t, s["max_tree_depth"], color=STRATEGY_COLOR[alg],
                label=f"{alg}: max")
        ax.plot(t, s["avg_tree_depth"], color=STRATEGY_COLOR[alg], linestyle=":",
                lw=0.7, label=f"{alg}: mean")
    # 20 levels is the deepest chain the mediasoup testbed actually measured; above it the
    # simulation is extrapolating, so mark the line rather than shading a region that can
    # swallow the whole plot when the run stays shallow.
    ax.axhline(20, color="#666666", lw=0.7, ls="--", zorder=1)
    ax.annotate("20 hops (validated on real media servers)", xy=(0.99, 20),
                xycoords=("axes fraction", "data"), ha="right", va="bottom",
                fontsize=4.8, color="#666666")
    depth_max = max(s["max_tree_depth"].max() for s in data.values())
    if depth_max >= 40:
        ax.set_yscale("log")
    else:
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_xlabel("Time (hours)")
    ax.set_ylabel("Interconnection depth (levels)")
    ax.grid(True, which="both", lw=0.3, alpha=0.4)
    ax.legend(ncol=2, frameon=False, columnspacing=0.9, handlelength=1.6)
    ax.set_title(f"{size} instance {instance}, $C$={capacity}")
    fig.tight_layout(pad=0.3)
    p = f"{out}/plots/depth_over_time_{size}_{capacity}{suffix}.png"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="steps_full/series")
    ap.add_argument("--summaries", default="steps_full/summary")
    ap.add_argument("--winners", default="tables/winners.json")
    ap.add_argument("--out", default="tables")
    ap.add_argument("--stats", default="tables/best_elite_runs.csv")
    ap.add_argument("--instance", type=int, default=30)
    ap.add_argument("--capacities", type=int, nargs="*", default=CAPACITIES)
    ap.add_argument("--start", type=float, help="window start, seconds of simulated time")
    ap.add_argument("--end", type=float, help="window end, seconds of simulated time")
    ap.add_argument("--suffix", default="", help="appended to output filenames")
    ap.add_argument("--no-depth-panel", action="store_true",
                    help="two-panel figure: viewers and servers, no depth panel")
    ap.add_argument("--combined", action="store_true",
                    help="one axes with viewers on a twin right axis, as the paper's Figure 4")
    args = ap.parse_args()

    df = drop_infeasible(load_summaries(args.summaries), args.stats)
    winners = json.load(open(args.winners))
    print(f"strategies available: {sorted(df.algorithm.unique())}")
    for size in SIZES:
        for cap in args.capacities:
            data = collect(df, args.series, size, cap, args.instance, winners,
                           args.start, args.end)
            if args.combined:
                figure_servers_combined(data, size, cap, args.instance, args.out,
                                        args.suffix)
            else:
                figure_servers_in_use(data, size, cap, args.instance, args.out,
                                      args.suffix, depth=not args.no_depth_panel)
            figure_depth_over_time(data, size, cap, args.instance, args.out, args.suffix)


if __name__ == "__main__":
    main()
