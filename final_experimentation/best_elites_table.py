#!/usr/bin/env python3
"""Emit the paper's `tables/best_elites.tex` from the tuning and evaluation outputs.

The paper's tables are hand-formatted ACM tables that carry the same numbers as the
notebooks' `pandas.to_latex` output, and the two sides have to be kept in step by hand.
That hand step is error-prone: twelve rows x eight parameters transcribed by eye, against
short codes that do not line up lexically with the paper's symbols.  This builds the table
instead.

Inputs:
  tables/best_elites.json      winner of each race          (elite_configs.py)
  tables/best_elite_runs.csv   per-run RPD and Rank         (final_evaluation.py)

The second is optional: without it every RPD/Rank cell is a dash, which is the right state
while the final evaluation is still running.

IDs are assigned in table order over *distinct* configurations, so a configuration winning
two cells gets one ID and appears twice.  The script reports how many distinct
configurations there are, because the paper states that count in prose and it has to match.

`RESERVATION_PER_SERVER` is `SER` in the tooling and `SRV` in the paper.  That mismatch is
real and long-standing; it is translated here, in one place.

Usage:
    python3 final_experimentation/best_elites_table.py \
        --elites tables/best_elites.json \
        --runs tables/best_elite_runs.csv \
        --out ../webrtc-scalability-strategies/tables/best_elites.tex
"""
import argparse
import json
import os
import re
import sys

import pandas as pd

SIZES = ["small", "medium", "big"]
CAPACITIES = [50, 150, 650, 1000]
SIZE_LABEL = {"small": "small", "medium": "med.", "big": "big"}
# The tooling writes SER; the paper writes SRV.  Everything else is spelled the same.
PAPER_VALUE = {"SER": "SRV"}
# Label short code -> paper column, in the paper's column order.
COLUMNS = ["MR", "RT", "EVST", "EVSP", "OVST", "OVSP", "NVSP", "PSP"]

LABEL_RE = re.compile(r"^([ABC])(\d+)\((.*)\)$")

HEADER = r"""\begin{table}
    \caption{Elite configurations for each instance size and server capacity. An ID is assigned to each configuration for easier reference. A comparison of over all instances and server capacities for each configuration using mean RPD and rank is also shown. }
    \label{tab:best_elites}
    \begin{center}
    {%
    \setlength{\tabcolsep}{2.5pt}%
    \begin{tabular}{|c|c|c|c|c|c|c|c|c|c|c|c|c|c|}
        \hline
        \makecell{\textbf{Inst.}\\\textbf{size}} & \makecell{\textbf{C}} & \makecell{\textbf{ID}} & \makecell{\textbf{Str.}} & \makecell{$C_{r}$} & \makecell{\textbf{$OCR$}} & \makecell{\textbf{$VA_{dtp}$}} & \makecell{\textbf{$VA_{ms}$}} & \makecell{\textbf{$OMS_{dtp}$}} & \makecell{\textbf{$OMS_{ms}$}} & \makecell{\textbf{$DMS_{ms}$}} & \makecell{\textbf{$PA_{ms}$}} & \makecell{\textbf{RPD (\%)}} & \makecell{\textbf{Rank}} \\
        \hline"""

FOOTER = r"""        \hline
    \end{tabular}%
    }%
    \end{center}
\end{table}"""


def parse_label(label):
    m = LABEL_RE.match(label.strip())
    if not m:
        raise ValueError(f"not a configuration label: {label!r}")
    strategy, _, body = m.groups()
    fields = dict(part.split("=", 1) for part in body.split(", "))
    return strategy, {k.strip(): v.strip() for k, v in fields.items()}


def cell_map(elites_path):
    """(size, capacity) -> winning label, from best_elites.json."""
    with open(elites_path) as fh:
        elites = json.load(fh)
    out = {}
    for label, cells in elites.items():
        for size, cap in cells:
            out[(size, int(cap))] = label
    return out


def stats_by_config(runs_path):
    """Configuration -> (mean RPD, mean Rank) over every test run of Strategy C."""
    if not runs_path or not os.path.exists(runs_path):
        return {}
    df = pd.read_csv(runs_path)
    if "Strategy" in df.columns:
        df = df[df["Strategy"] == "C"]
    g = df.groupby("Configuration")
    return {cfg: (round(float(g.get_group(cfg)["RPD"].mean()), 3),
                  round(float(g.get_group(cfg)["Rank"].mean()), 3))
            for cfg in g.groups}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elites", default="tables/best_elites.json")
    ap.add_argument("--runs", default="tables/best_elite_runs.csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cells = cell_map(args.elites)
    stats = stats_by_config(args.runs)
    if stats:
        print(f"RPD/Rank read for {len(stats)} configurations", file=sys.stderr)
    else:
        print("no evaluation runs yet -- RPD/Rank columns will be dashes",
              file=sys.stderr)

    # IDs over distinct configurations, in table order.
    ids, next_id = {}, 1
    ordered = [(s, c) for s in SIZES for c in CAPACITIES if (s, c) in cells]
    for cell in ordered:
        label = cells[cell]
        if label not in ids:
            ids[label] = next_id
            next_id += 1

    best_rpd = min((v[0] for v in stats.values()), default=None)
    best_rank = min((v[1] for v in stats.values()), default=None)

    lines = []
    for size, cap in ordered:
        label = cells[(size, cap)]
        strategy, fields = parse_label(label)
        values = [PAPER_VALUE.get(fields[c], fields[c]) for c in COLUMNS]
        rpd, rank = stats.get(label, (None, None))
        rpd_s = "-" if rpd is None else (
            f"\\textbf{{{rpd}}}" if rpd == best_rpd else f"{rpd}")
        rank_s = "-" if rank is None else (
            f"\\textbf{{{rank}}}" if rank == best_rank else f"{rank}")
        cols = [SIZE_LABEL[size], str(cap), str(ids[label]), strategy] + values
        lines.append("        " + " & ".join(cols + [rpd_s, rank_s]) + r"\\")

    body = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write(f"{HEADER}\n{body}\n{FOOTER}\n")

    n_distinct = len(ids)
    print(f"wrote {args.out}: {len(ordered)} cells, {n_distinct} distinct configurations",
          file=sys.stderr)
    if n_distinct < len(ordered):
        for label, i in ids.items():
            where = [f"{s}/C={c}" for (s, c) in ordered if cells[(s, c)] == label]
            if len(where) > 1:
                print(f"  ID {i} wins {len(where)} cells: {', '.join(where)}",
                      file=sys.stderr)
    else:
        print("  every cell has a distinct winner", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
