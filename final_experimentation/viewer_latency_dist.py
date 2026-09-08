#!/usr/bin/env python3
"""Where viewers actually sit in the latency distribution.

`depth_analysis.py` reports depth per *run* -- a peak, a time-weighted mean, percentiles of
the time distribution. None of those answer the question a reviewer actually asks: **what
fraction of viewing time is spent at what latency?**  A 34 ms worst case that one viewer meets
for eight seconds is a different result from one that half the audience sits in.

This script builds the viewer-weighted distribution. Each interval between consecutive steps
contributes weight `viewers x dt` (viewer-seconds) at that interval's depth, so the resulting
percentiles are over *aggregate viewing time*, not over runs.

Two depth columns, deliberately both:

  * `max_tree_depth` -- the deepest tree anywhere on the platform at that instant. Every
    viewer is charged the worst tree in the system, so this is a hard **upper bound**.
  * `avg_tree_depth` -- the mean depth over the live session trees. A **central estimate**.

Neither is a true per-viewer hop count: the simulator records per-session tree depth, not
which level each viewer attached to. The true distribution lies between these two curves and
is closer to the lower one. Producing it exactly needs new simulator instrumentation.

Depth converts to latency as `beta * (depth - 1)`, beta from mediasoup campaign v2.

Usage:
    python3 final_experimentation/viewer_latency_dist.py \
        --root steps_full --winners tables/winners.json --out tables
"""
import argparse
import glob
import gzip
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import gaussian_kde

# mediasoup campaign v2: in-VPC per-hop cost, trimmed pooled cluster bootstrap
BETA, BETA_LO, BETA_HI = 0.0910, 0.0540, 0.1269
MEASURED_MAX_HOPS = 149

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]

# Capacity is an ordered magnitude -> ORDINAL ramp: one hue, monotone lightness, and the
# light end clears 2:1 on the surface. Steps 700/550/400/250 of the reference blue ramp;
# passes validate_palette.js --ordinal in both light and dark.
CAP_COLOR = {50: "#0d366b", 150: "#1c5cab", 650: "#3987e5", 1000: "#86b6ef"}
INK, MUTED, GRID = "#1a1a19", "#5c5c58", "#e0dfdb"

FIG_DPI = 400
YTOP = 36.0    # shared y ceiling; recomputed from the data in main()


def load_summaries(root):
    for f in sorted(glob.glob(os.path.join(root, "summary", "*.jsonl"))):
        for line in open(f):
            yield json.loads(line)


def series_path(root, rec):
    base = rec["member"].rsplit(".csv", 1)[0] + ".series.csv.gz"
    for d in (rec["instance_type"], f"a_{rec['instance_type']}", f"b_{rec['instance_type']}"):
        p = os.path.join(root, "series", d, base)
        if os.path.exists(p):
            return p
    return None


def run_weights(path):
    """(max_depth, avg_depth, viewer_second_weight) per held interval, zero-order hold."""
    with gzip.open(path, "rt") as g:
        g.readline()
        ts, vw, md, ad = [], [], [], []
        for line in g:
            p = line.split(",")
            ts.append(float(p[0])); vw.append(float(p[7]))
            md.append(float(p[8])); ad.append(float(p[9]))
    if len(ts) < 2:
        return None
    ts = np.asarray(ts); vw = np.asarray(vw)
    md = np.asarray(md); ad = np.asarray(ad)
    dt = np.diff(ts)
    w = dt * vw[:-1]
    keep = w > 0
    if not keep.any():
        return None
    return md[:-1][keep], ad[:-1][keep], w[keep]


def pooled(root, recs, which="max"):
    V, W = [], []
    for r in recs:
        p = series_path(root, r)
        if not p:
            continue
        res = run_weights(p)
        if res is None:
            continue
        md, ad, w = res
        V.append(md if which == "max" else ad)
        W.append(w)
    if not V:
        return None, None
    return np.concatenate(V), np.concatenate(W)


def wpct(vals, w, qs):
    o = np.argsort(vals)
    v, ww = vals[o], w[o]
    c = np.cumsum(ww)
    tot = c[-1]
    if tot <= 0:
        return [np.nan] * len(qs)
    return [float(v[np.searchsorted(c, q * tot)]) for q in qs]


def ms(depth):
    return (np.asarray(depth) - 1) * BETA


def violin(ax, x, vals, weights, color, width=0.36, label_mean=True):
    """Weighted violin on a LINEAR axis.

    Depth-1 intervals (a session on one media server, exactly 0 ms) are kept here -- on a
    linear axis zero is representable, so nothing needs excluding.

    The p99 tick is drawn at the violin's own half-width at that height rather than at a
    fixed marker size: a fixed-width marker is wider than the violin near the tail and reads
    as a line cutting the tip off.
    """
    lat = ms(vals)
    if len(lat) == 0 or weights.sum() <= 0:
        return
    if lat.max() - lat.min() < 1e-9:
        ax.plot([x - width, x + width], [lat[0]] * 2, color=color, lw=2)
        return
    kde = gaussian_kde(lat, weights=weights, bw_method=0.25)
    grid = np.linspace(max(lat.min(), 0.0), lat.max(), 400)
    dens = kde(grid)
    dens = dens / dens.max() * width
    ax.fill_betweenx(grid, x - dens, x + dens, color=color,
                     alpha=0.85, lw=0.6, edgecolor=color, zorder=2)

    p50, p99 = wpct(vals, weights, [0.50, 0.99])
    m50, m99 = ms(p50), ms(p99)
    hw50 = float(np.interp(m50, grid, dens))
    hw99 = float(np.interp(m99, grid, dens))
    ax.plot([x - hw50 * 0.85, x + hw50 * 0.85], [m50] * 2,
            color="#fcfcfb", lw=1.6, solid_capstyle="butt", zorder=4)
    ax.plot([x - hw99, x + hw99], [m99] * 2,
            color="#fcfcfb", lw=1.0, solid_capstyle="butt", zorder=4)

    if label_mean:
        mean = float((np.asarray(ms(vals)) * weights).sum() / weights.sum())
        ax.annotate(f"{mean:.2f}", (x, mean), textcoords="offset points", xytext=(0, 0),
                    ha="center", va="center", fontsize=6, color=INK, zorder=6,
                    bbox=dict(boxstyle="round,pad=0.16", fc="#fcfcfb", ec="none", alpha=0.88))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="steps_full")
    ap.add_argument("--winners", default="tables/winners.json")
    ap.add_argument("--out", default="tables")
    args = ap.parse_args()

    winners = json.load(open(args.winners))
    recs = [r for r in load_summaries(args.root) if winners.get(r["algorithm"]) == r["config"]]
    C = [r for r in recs if r["algorithm"] == "C"]
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "plots"), exist_ok=True)

    # ---- table ------------------------------------------------------------------
    QS = [0.50, 0.90, 0.99, 0.999]
    lines = []
    for which, label in (("max", "max_tree_depth (upper bound)"),
                         ("avg", "avg_tree_depth (central estimate)")):
        lines.append(f"\n{label}")
        lines.append(f"{'size':7s}{'C':>6s} | " + " ".join(f"{n:>7s}" for n in
                     ("p50", "p90", "p99", "p99.9", "max")) + " |   <10ms")
        for size in SIZES:
            for cap in CAPACITIES:
                sel = [r for r in C if r["instance_type"] == size and r["capacity"] == cap]
                v, w = pooled(args.root, sel, which)
                if v is None:
                    continue
                ps = wpct(v, w, QS)
                d10 = 10 / BETA + 1
                lines.append(f"{size:7s}{cap:6d} | " +
                             " ".join(f"{ms(p):7.2f}" for p in ps) +
                             f" {ms(v.max()):7.2f} | {100 * w[v <= d10].sum() / w.sum():7.2f}%")
        v, w = pooled(args.root, C, which)
        ps = wpct(v, w, QS)
        lines.append(f"{'ALL':13s} | " + " ".join(f"{ms(p):7.2f}" for p in ps) +
                     f" {ms(v.max()):7.2f} |")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(args.out, "viewer_latency_percentiles.txt"), "w") as f:
        f.write(report + "\n")

    # ---- figure -----------------------------------------------------------------
    tops = {}
    for size in SIZES:
        t = 0.0
        for cap in CAPACITIES:
            sel = [r for r in C if r["instance_type"] == size and r["capacity"] == cap]
            v, w = pooled(args.root, sel, "max")
            if v is not None:
                t = max(t, float(ms(v).max()))
        tops[size] = t * 1.06
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.8))
    for ax, size in zip(axes, SIZES):
        for i, cap in enumerate(CAPACITIES):
            sel = [r for r in C if r["instance_type"] == size and r["capacity"] == cap]
            v, w = pooled(args.root, sel, "max")
            if v is None:
                continue
            violin(ax, i, v, w, CAP_COLOR[cap])
        ax.set_xticks(range(len(CAPACITIES)))
        ax.set_xticklabels([str(c) for c in CAPACITIES])
        ax.set_title(size, fontsize=8, color=INK, pad=4)
        ax.set_xlabel("media server capacity $C$", fontsize=7, color=MUTED)
        ax.set_ylim(0, tops[size])
        ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=7, length=2)
    for ax in axes:
        ax.set_ylabel("added latency (ms)", fontsize=7, color=MUTED)
    fig.suptitle("Where viewing time sits in the latency distribution — Strategy C cost-winner",
                 fontsize=8.5, color=INK, y=1.02)
    fig.text(0.5, -0.10,
             "Violin width = share of aggregate viewing time (viewer-seconds) at that latency. "
             "White bar = median, thin tick = p99, number = mean. Note the per-panel y scale.\n"
             "Charged at the deepest tree on the platform, so an upper bound.",
             ha="center", fontsize=6, color=MUTED)
    fig.tight_layout()
    out = os.path.join(args.out, "plots", "viewer_latency_violin.png")
    fig.savefig(out, dpi=FIG_DPI, bbox_inches="tight", facecolor="#fcfcfb")
    print(f"\nwrote {out}")

    # ---- second figure: the two bounds, pooled -----------------------------------
    fig2, ax = plt.subplots(figsize=(3.6, 2.7))
    for i, (which, lab, col) in enumerate((("max", "deepest tree\n(upper bound)", "#0d366b"),
                                           ("avg", "mean tree depth\n(central est.)", "#3987e5"))):
        v, w = pooled(args.root, C, which)
        # violin() already draws the median bar and labels the mean; a second annotation
        # here collided with the tick labels once the axis went linear.
        violin(ax, i, v, w, col, width=0.30)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["deepest tree\n(upper bound)", "mean tree depth\n(central est.)"], fontsize=7)
    ax.set_ylim(0, YTOP)
    ax.set_ylabel("added latency (ms)", fontsize=7, color=MUTED)
    ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7, length=2)
    ax.set_title("All 120 winner runs pooled\n(number = mean, bar = median)", fontsize=8, color=INK)
    fig2.tight_layout()
    out2 = os.path.join(args.out, "plots", "viewer_latency_bounds.png")
    fig2.savefig(out2, dpi=FIG_DPI, bbox_inches="tight", facecolor="#fcfcfb")
    print(f"wrote {out2}")


if __name__ == "__main__":
    main()
