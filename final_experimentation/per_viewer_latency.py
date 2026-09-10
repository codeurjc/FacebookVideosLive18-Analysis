#!/usr/bin/env python3
"""Per-VIEWER latency distribution: every viewer counted once.

`viewer_latency_dist.py` weights by viewer-SECONDS, so a viewer who watches for two hours
counts 24x one who watches five minutes. That answers "what fraction of viewing TIME is spent
at what latency". It does not answer "what fraction of VIEWERS meet what latency", which is
what a reviewer means by "99.9 % of viewers".

This script answers the second question. It reconstructs every viewer's own
[join, leave] interval from the instance event stream, time-averages the depth over exactly
that interval, and then takes percentiles over VIEWERS -- each contributing one observation
regardless of how long they stayed.

Depth semantics are unchanged and still bracketed (see viewer_latency_dist.py):
`max_tree_depth` charges every viewer the deepest tree on the platform (upper bound),
`avg_tree_depth` the mean over live trees (central estimate). The simulator does not record
which level a viewer attached to, so a true per-viewer hop count needs new instrumentation.

Memory is bounded: the join dict holds only currently-connected viewers, and results are
accumulated into a fixed histogram rather than a per-viewer array, so a 16 M-viewer big
instance costs no more than a small one.

Usage:
    python3 final_experimentation/per_viewer_latency.py --sizes small medium big --out tables
"""
import argparse
import gzip
import json
import os

import numpy as np

BETA = 0.0910
BINS_PER_UNIT = 10          # depth resolution 0.1 level = 0.009 ms
MAX_DEPTH = 4000


def viewer_intervals(inst_dir, size, inst):
    """Stream one instance CSV -> (join[], leave[]) arrays, one entry per viewer."""
    path = os.path.join(inst_dir, f"instances-{size}", f"instance-{size}-{inst}.csv")
    active, joins, leaves = {}, [], []
    last = 0.0
    with open(path, "r", buffering=1 << 22) as f:
        f.readline()
        for line in f:
            c = line[0]
            if c == "1":
                p = line.split(",", 3)
                t = float(p[2]); last = t
                active[p[3] + p[1]] = len(joins)
                joins.append(t); leaves.append(-1.0)
            elif c == "3":
                p = line.split(",", 3)
                t = float(p[2]); last = t
                i = active.pop(p[3] + p[1], None)
                if i is not None:
                    leaves[i] = t
            else:
                last = max(last, float(line.split(",", 3)[2]))
    j = np.asarray(joins); l = np.asarray(leaves)
    l[l < 0] = last                       # never left -> connected until the run ends
    return j, l


def series_depth(path, col):
    with gzip.open(path, "rt") as g:
        g.readline()
        t, d = [], []
        k = 8 if col == "max" else 9
        for line in g:
            p = line.split(",")
            t.append(float(p[0])); d.append(float(p[k]))
    return np.asarray(t), np.asarray(d)


def per_viewer_mean(j, l, t, d):
    """Time-average of the depth step function over each viewer's own interval."""
    I = np.concatenate([[0.0], np.cumsum(np.diff(t) * d[:-1])])
    lo, hi = t[0], t[-1]
    jj = np.clip(j, lo, hi); ll = np.clip(l, lo, hi)
    dur = ll - jj
    out = np.empty_like(dur)
    ok = dur > 0
    out[ok] = (np.interp(ll[ok], t, I) - np.interp(jj[ok], t, I)) / dur[ok]
    if (~ok).any():
        idx = np.clip(np.searchsorted(t, jj[~ok], side="right") - 1, 0, len(d) - 1)
        out[~ok] = d[idx]
    return out


class Hist:
    """Fixed-width histogram over depth, so memory does not grow with viewer count."""
    def __init__(self):
        self.h = np.zeros(MAX_DEPTH * BINS_PER_UNIT + 2, dtype=np.int64)

    def add(self, v):
        idx = np.clip((v * BINS_PER_UNIT).astype(np.int64), 0, len(self.h) - 1)
        self.h += np.bincount(idx, minlength=len(self.h))

    @property
    def n(self):
        return int(self.h.sum())

    def pct(self, qs):
        c = np.cumsum(self.h); tot = c[-1]
        return [float(np.searchsorted(c, q * tot) / BINS_PER_UNIT) for q in qs]

    def frac_below(self, depth):
        i = min(int(depth * BINS_PER_UNIT), len(self.h) - 1)
        return float(self.h[:i + 1].sum() / max(self.h.sum(), 1))

    def max(self):
        return float(np.nonzero(self.h)[0][-1] / BINS_PER_UNIT)


def ms(x):
    return (np.asarray(x) - 1) * BETA


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="steps_full")
    ap.add_argument("--instances", default="instances")
    ap.add_argument("--winners", default="tables/winners.json")
    ap.add_argument("--sizes", nargs="+", default=["small", "medium", "big"])
    ap.add_argument("--out", default="tables")
    args = ap.parse_args()

    W = json.load(open(args.winners))
    caps = [50, 150, 650, 1000]
    result = {}
    for size in args.sizes:
        recs = [json.loads(l) for l in open(os.path.join(args.root, "summary", f"{size}.jsonl"))]
        recs = [r for r in recs if r["config"] == W["C"]]
        H = {(c, w): Hist() for c in caps for w in ("max", "avg")}
        for inst in sorted({r["instance"] for r in recs}):
            j, l = viewer_intervals(args.instances, size, inst)
            for r in [x for x in recs if x["instance"] == inst]:
                sp = os.path.join(args.root, "series", size,
                                  r["member"].rsplit(".csv", 1)[0] + ".series.csv.gz")
                for w in ("max", "avg"):
                    t, d = series_depth(sp, w)
                    H[(r["capacity"], w)].add(per_viewer_mean(j, l, t, d))
            print(f"  {size}-{inst}: {len(j):,} viewers", flush=True)
        result[size] = H

    QS = [0.5, 0.9, 0.99, 0.999]
    out = []
    for w, lab in (("max", "max_tree_depth (upper bound)"),
                   ("avg", "avg_tree_depth (central estimate)")):
        out.append(f"\n{lab}   -- percentiles over VIEWERS, each counted once")
        out.append(f"{'size':7s}{'C':>6s} | {'viewers':>13s} | " +
                   " ".join(f"{n:>7s}" for n in ("p50", "p90", "p99", "p99.9", "max")) + " |  <10ms")
        for size in args.sizes:
            for c in caps:
                h = result[size][(c, w)]
                d10 = 10 / BETA + 1
                out.append(f"{size:7s}{c:6d} | {h.n:13,} | " +
                           " ".join(f"{ms(x):7.2f}" for x in h.pct(QS)) +
                           f" {ms(h.max()):7.2f} | {100*h.frac_below(d10):6.2f}%")
        tot = Hist()
        for size in args.sizes:
            for c in caps:
                tot.h += result[size][(c, w)].h
        out.append(f"{'ALL':13s} | {tot.n:13,} | " +
                   " ".join(f"{ms(x):7.2f}" for x in tot.pct(QS)) + f" {ms(tot.max()):7.2f} |")
        for thr in (1, 5, 10, 25, 50):
            out.append(f"    viewers under {thr:3d} ms: {100*tot.frac_below(thr/BETA+1):7.3f}%")
    rep = "\n".join(out)
    print(rep)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "per_viewer_latency.txt"), "w") as f:
        f.write(rep + "\n")
    np.savez(os.path.join(args.out, "per_viewer_hists.npz"),
             **{f"{s}_{c}_{w}": result[s][(c, w)].h
                for s in args.sizes for c in caps for w in ("max", "avg")})


if __name__ == "__main__":
    main()
