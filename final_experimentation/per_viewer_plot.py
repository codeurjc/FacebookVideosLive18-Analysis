#!/usr/bin/env python3
"""Violin plots of the per-VIEWER latency distribution, from per_viewer_latency.py's histograms.

Each viewer contributes one observation regardless of watch duration, so the violin width is
the share of VIEWERS at that latency -- not the share of viewing time.
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import gaussian_kde

BETA = 0.0910
BINS_PER_UNIT = 10
SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]
# ordinal ramp (capacity is an ordered magnitude); passes validate_palette.js --ordinal
CAP_COLOR = {50: "#0d366b", 150: "#1c5cab", 650: "#3987e5", 1000: "#86b6ef"}
YTOP = 36.0
# Print target: all text black on a pure-white ground, so the figure stays legible
# on paper. The grid is the one recessive element, kept light but dark enough to print.
INK, MUTED, GRID = "#000000", "#000000", "#cccccc"
AXIS = "#000000"
SURFACE = "#ffffff"


def depths_weights(h):
    """Histogram -> (depth values, viewer counts). Nothing is dropped: a linear axis can
    represent the depth-1 (exactly 0 ms) mass, which a log axis could not."""
    idx = np.nonzero(h)[0]
    return idx / BINS_PER_UNIT, h[idx].astype(float)


def wpct(v, w, qs):
    o = np.argsort(v); v, w = v[o], w[o]
    c = np.cumsum(w); tot = c[-1]
    return [float(v[np.searchsorted(c, q * tot)]) for q in qs]


def ms(x):
    return (np.asarray(x) - 1) * BETA


def violin(ax, x, vals, weights, color, width=0.36):
    """Weighted violin on a linear axis, with a median bar and nothing else.

    The median bar is drawn at the violin's own half-width at that height; a fixed-size
    marker is wider than the shape near the tail and reads as a line cutting it.
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
    # Median as a thin black rule, drawn to the violin's own half-width at that height so
    # it never overhangs the shape. Thin enough not to swamp the flat, wide violins
    # (big/C=1000, medium/C=650), where a heavy bar bisects the outline.
    m50 = ms(wpct(vals, weights, [0.50])[0])
    hw = float(np.interp(m50, grid, dens))
    ax.plot([x - hw, x + hw], [m50] * 2,
            color="#000000", lw=0.8, solid_capstyle="butt", zorder=5)

def trim_vertical_margin(path):
    """Crop fully-blank pixel rows from the top and bottom of the saved figure.

    LaTeX adds its own vertical space around a float; any blank band baked into the image
    is added on top of that and shows up as an over-spaced figure on the page. Width is
    left untouched so the image still scales cleanly to \\textwidth.
    """
    from PIL import Image
    import numpy as np
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    ink = (a < 250).any(axis=(1, 2))          # rows holding anything but the ground
    rows = np.nonzero(ink)[0]
    if len(rows) == 0:
        return
    im.crop((0, int(rows[0]), a.shape[1], int(rows[-1]) + 1)).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hists", default="tables/per_viewer_hists.npz")
    ap.add_argument("--out", default="tables/plots")
    ap.add_argument("--which", default="max", choices=["max", "avg"])
    args = ap.parse_args()
    z = np.load(args.hists)
    os.makedirs(args.out, exist_ok=True)

    # one shared ceiling across all panels, from the data -- sharey alone autoscales off
    # the first panel and silently clips the others
    # Per-PANEL ceiling. One shared linear axis is driven by medium/C=50's tail and
    # flattens the other ten violins to nothing; the distribution is too right-skewed for
    # a single linear scale. Each panel therefore carries its own labelled axis.
    tops = {}
    for size in SIZES:
        t = 0.0
        for cap in CAPACITIES:
            k = f"{size}_{cap}_{args.which}"
            if k in z:
                d, w = depths_weights(z[k])
                if len(d):
                    t = max(t, float(ms(d).max()))
        tops[size] = t * 1.06

    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.05))
    for ax, size in zip(axes, SIZES):
        for i, cap in enumerate(CAPACITIES):
            key = f"{size}_{cap}_{args.which}"
            if key not in z:
                continue
            d, w = depths_weights(z[key])
            violin(ax, i, d, w, CAP_COLOR[cap])
        ax.set_xticks(range(len(CAPACITIES)))
        ax.set_xticklabels([str(c) for c in CAPACITIES])
        ax.set_title(size, fontsize=9.5, color=INK, pad=3)
        ax.set_xlabel("media server capacity $C$", fontsize=8.5, color=MUTED, labelpad=2)
        ax.set_ylim(0, tops[size])
        ax.grid(axis="y", color=GRID, lw=0.6, zorder=0); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(AXIS)
        ax.tick_params(colors=AXIS, labelsize=8, length=2.5, pad=1.5)
    for ax in axes:
        ax.set_ylabel("added latency (ms)", fontsize=8.5, color=MUTED, labelpad=2)
    fig.suptitle("Latency per viewer", fontsize=11, color=INK, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.93), pad=0.4, w_pad=1.0)
    out = os.path.join(args.out, "per_viewer_latency_violin.png")
    fig.savefig(out, dpi=400, bbox_inches="tight", pad_inches=0, facecolor=SURFACE)
    trim_vertical_margin(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
