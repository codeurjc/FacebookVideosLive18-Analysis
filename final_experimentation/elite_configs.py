#!/usr/bin/env python3
"""Elite configurations, read straight out of a tree of irace.Rdata files.

This is the part of `irace.ipynb` that loads `irace_results/**/irace.Rdata` through rpy2
and summarises each race's final elites, lifted out of the notebook so it can be run from a
shell without R or Jupyter.  The statistics are the notebook's:

  Mean RPD   per instance, 100 * (cost - best cost) / ((cost + best cost) / 2) against the
             best of *all* configurations tried in that race, then averaged over the
             instances the configuration was actually run on
  Mean Rank  rank of the configuration's cost among the final elites, per instance, ties
             taking the minimum rank, then averaged

Both are computed over training and test instances together, as in the notebook.

Two things it does that the notebook does not:

  * It reads `race.json` when a race directory has one, so the (kind, size, capacity,
    budget) of a race comes from what the runner recorded rather than from the shape of
    the path.  Trees without `race.json` still fall back to path parsing.
  * `TOP_TO_BOTTOM` is translated.  The notebook's `tree_traversal_translation_map` is
    keyed on `"TTB"`, which is not a value irace ever stores, so every top-to-bottom
    traversal silently became NaN and vanished from the notebook's configuration strings.
    The paper is unaffected -- its labels come from the simulator's hand-written
    `run-alg-parameters.txt.*` -- but the notebook's own elite tables were missing the
    EVST/OVST/VAdtp/OMSdtp columns wherever the winner was top-to-bottom.

Races that did not finish are skipped with a note: a crashed race leaves an `.Rdata` that
is an iteration checkpoint, with no `allElites` and no `testing` block, useful only to
irace's own `--recovery-file`.

All three strategy sets are read by default, because the results tree contains all three:
the `all` races, where irace picks the strategy as well as its parameters, and the A-only
and B-only races, which answer "how well can A and B do when tuned in isolation" and are
what the strategy comparison is built on.  `--kinds all` restricts the run to the `all`
races, which are the ones the final evaluation and the paper's elite table use.

`best_elites.json` is always written from the `all` races alone: the A and B races cover
the same (instance size, capacity) cells, so including them would put two winners in one
cell.

Usage:
    python3 final_experimentation/elite_configs.py --results irace_results --out tables
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

import rpy2.robjects as ro
import rpy2.rinterface_lib.sexp as SEXP
from rpy2.robjects import pandas2ri
from rpy2.robjects.packages import importr
from rpy2.robjects.vectors import ListVector

# --- the notebook's naming tables (cell 1), with the TOP_TO_BOTTOM fix -----------------

PARAMETER_TRANSLATION_MAP = {
    "A_viewerServerSelectionDistribution": "SP",
    "B_maxReservation": "MR [B]",
    "B_existingViewerServerSelectionDistribution": "VAms",
    "B_newViewerServerParentSelectionDistribution": "OMSms",
    "B_existingViewerServerSelectionTreeTraversal": "VAdtp",
    "B_newViewerServerParentSelectionTreeTraversal": "OMSdtp",
    "C_maxReservation": "MR [C]",
    "C_reservationType": "RT",
    "C_alreadyLinkedTreeTraversal": "EVST",
    "C_alreadyLinkedDistribution": "EVSP",
    "C_linkedSpaceTreeTraversal": "OVST",
    "C_linkedSpaceDistribution": "OVSP",
    "C_viewerAndLinkFromDistribution": "NVSP",
    "C_publisherServerSelectionDistribution": "PSP",
    "maxReservation": "MR",
}
DISTRIBUTION_COLS = ["SP", "ESP", "OSP", "EVSP", "OVSP", "NVSP", "PSP", "VAms", "OMSms"]
TREE_TRAVERSAL_COLS = ["EST", "OST", "EVST", "OVST", "VAdtp", "OMSdtp"]
DISTRIBUTION_TRANSLATION_MAP = {
    "LOW_LOAD": "LL",
    "HIGH_LOAD": "HL",
    "ROUND_ROBIN": "RR",
    "RANDOM": "RND",
}
TREE_TRAVERSAL_TRANSLATION_MAP = {
    "TOP_TO_BOTTOM": "TTB",  # the notebook keys this on "TTB" and so drops the column
    "TTB": "TTB",
    "BOTTOM_TO_TOP": "BTT",
    "IGNORE_TREE_LEVEL": "IT",
}
RESERVATION_TYPE_TRANSLATION_MAP = {
    "RESERVATION_PER_SERVER": "SER",
    "RESERVATION_PER_SESSION": "SES",
}

SIZE_ORDER = {"small": 0, "medium": 1, "big": 2}
IRACE_INT_NA = -2147483648


# --- loading (cell 2) -----------------------------------------------------------------


def remove_duplicates(configs, training_exps, testing_exps, elites):
    """Collapse configurations irace tried more than once, keeping the best-covered one.

    Verbatim from the notebook: duplicates that contain a RND parameter are not really
    duplicates (two draws of RANDOM are two different things), so they are exempted.
    """
    duplicate_configs = configs[configs.duplicated(keep=False)]
    duplicate_configs = duplicate_configs[
        ~duplicate_configs.apply(
            lambda row: any(row[col] == "RND" for col in duplicate_configs.columns),
            axis=1,
        )
    ]
    duplicate_groups = {}
    for idx, row in duplicate_configs.iterrows():
        row_key = frozenset(
            (col, str(row[col]) if pd.notna(row[col]) else "NaN")
            for col in duplicate_configs.columns
        )
        duplicate_groups.setdefault(row_key, []).append(idx)

    duplicate_indices_tuples = [
        tuple(indices) for indices in duplicate_groups.values() if len(indices) > 1
    ]

    best_cols = []
    for indices in duplicate_indices_tuples:
        cols = training_exps[list(indices)]
        if testing_exps.columns.isin(indices).any():
            best_col = next((i for i in indices if i in testing_exps.columns), None)
        else:
            best_col = cols.notna().sum().idxmax()
        best_cols.append(best_col)
        for other in indices:
            if other == best_col:
                continue
            for row in training_exps.index:
                if pd.isna(training_exps.at[row, best_col]) and pd.notna(
                    training_exps.at[row, other]
                ):
                    training_exps.at[row, best_col] = training_exps.at[row, other]

    updated_elites = list(elites)
    for indices, best_col in zip(duplicate_indices_tuples, best_cols):
        for dup in indices:
            if dup != best_col and dup in updated_elites:
                updated_elites[updated_elites.index(dup)] = best_col

    to_drop = [
        idx for grp in duplicate_indices_tuples for idx in grp if idx not in best_cols
    ]
    return configs.drop(index=to_drop), training_exps.drop(columns=to_drop), updated_elites


def calc_dev(full_exps, dev):
    """Per instance, each configuration's symmetric % deviation from that row's best."""
    for idx, row in full_exps.iterrows():
        min_value = row.min()
        dev.loc[idx] = row.apply(
            lambda x, m=min_value: (
                100 * (x - m) / ((x + m) / 2) if pd.notna(x) else x
            )
        )


def load_race(file_location, irace):
    """Return (configs, elite_ids, all_exps, dev) for one finished race."""
    ro.r["load"](file_location)
    objects = ro.r["iraceResults"]

    all_elites = objects.rx2("allElites")
    if all_elites == ro.NULL or len(all_elites) == 0:
        raise ValueError("no allElites -- iteration checkpoint, not a finished race")
    testing = objects.rx2("testing")
    if testing == ro.NULL:
        raise ValueError("no testing block -- iteration checkpoint, not a finished race")

    testing = ListVector(testing)
    test_exp_matrix = testing.rx2("experiments")
    test_matrix_row_names = list(test_exp_matrix.rownames)
    test_matrix_col_names = list(test_exp_matrix.colnames)
    training_exps = objects.rx2("experiments")
    training_exps_col_names = list(training_exps.colnames)
    # One row per training instance irace actually got to.  A race that converged early
    # -- the per-algorithm A races finish in minutes -- has fewer rows than the 30
    # training instances, so the instance labels have to be cut to the matrix, not to
    # `len()` of the R matrix (which is its element count).
    training_exps_nrow = int(ro.r["nrow"](training_exps)[0])

    with (ro.default_converter + pandas2ri.converter).context():
        instances = irace.get_instanceID_seed_pairs(objects, instances=True)
        test_exp_matrix = pd.DataFrame(
            pandas2ri.rpy2py(test_exp_matrix),
            index=test_matrix_row_names,
            columns=test_matrix_col_names,
        )
        training_exps = pd.DataFrame(
            pandas2ri.rpy2py(training_exps),
            index=instances["instanceID"][:training_exps_nrow],
            columns=training_exps_col_names,
        ).sort_index()

        configs = objects.rx2("allConfigurations")
        configs = configs.replace(IRACE_INT_NA, None)
        for col in configs.columns:
            configs[col] = configs[col].apply(
                lambda x: None if isinstance(x, SEXP.NACharacterType) else x
            )
        configs = configs.drop(columns=[".ID.", ".PARENT."])
        configs = configs.rename(columns=PARAMETER_TRANSLATION_MAP)
        for col in configs.select_dtypes(include=["float"]):
            if (configs[col] % 1 == 0).all():
                configs[col] = configs[col].astype("Int64")
        for col in configs.columns:
            if col in DISTRIBUTION_COLS:
                configs[col] = configs[col].map(DISTRIBUTION_TRANSLATION_MAP)
            if col in TREE_TRAVERSAL_COLS:
                configs[col] = configs[col].map(TREE_TRAVERSAL_TRANSLATION_MAP)
            if col == "RT":
                configs[col] = configs[col].map(RESERVATION_TYPE_TRANSLATION_MAP)

        elites = [str(e) for e in all_elites[-1]]
        configs, training_exps, elites = remove_duplicates(
            configs, training_exps, test_exp_matrix, elites
        )

        all_exps = pd.concat([training_exps, test_exp_matrix], axis=0)
        dev = all_exps.copy()
        calc_dev(all_exps, dev)
        return configs, elites, all_exps, dev


# --- summarising (cell 3) -------------------------------------------------------------


def config_summary_str(config_row, subindex=None, alg=None):
    """`C1(MR=3, RT=SER, EVST=TTB, ...)` -- the join key used by the evaluation logs."""
    algorithm = alg if alg is not None else config_row["algorithm"]
    result = f"{algorithm}{subindex}(" if subindex is not None else f"{algorithm}("
    for col in config_row.index:
        if col == "algorithm":
            continue
        value = config_row[col]
        if pd.isna(value):
            continue
        if isinstance(value, (float, np.floating)):
            value = int(value)
        result += f"{'MR' if 'MR' in col else col}={value}, "
    return result[:-2] + ")"


def summarise_race(configs, elites, all_exps, dev, alg):
    """One row per elite, ordered as irace ordered them (elite 1 is irace's pick)."""
    best_exps = all_exps[elites]
    rank_totals = np.zeros(len(elites))
    for _, row in best_exps.iterrows():
        ranks = row.rank(method="min")
        for i, idx in enumerate(elites):
            if pd.notna(ranks[idx]):
                rank_totals[i] += ranks[idx]

    rows = []
    for i, idx in enumerate(elites):
        rows.append(
            {
                "Elite": i + 1,
                "Config ID": idx,
                "Configuration": config_summary_str(
                    configs.loc[idx], subindex=i + 1, alg=None if alg == "all" else alg
                ),
                "Mean RPD": round(float(dev[idx].mean()), 3),
                "Mean Rank": round(float(rank_totals[i] / len(best_exps)), 3),
                "Instances run on": int(all_exps[idx].notna().sum()),
            }
        )
    return rows


# --- walking a results tree -----------------------------------------------------------


def describe_race(rdata_path, results_root):
    """(kind, size, capacity, budget) from race.json if present, else from the path."""
    race_dir = os.path.dirname(rdata_path)
    meta_path = os.path.join(race_dir, "race.json")
    if os.path.exists(meta_path):
        with open(meta_path) as fh:
            meta = json.load(fh)
        return (
            meta["kind"],
            meta["size"],
            int(meta["capacity"]),
            int(meta["budget"]),
            meta.get("host"),
            meta.get("seconds"),
        )
    # published layout: <root>/<budget>_maxExp/<size>/<capacity>/ and
    #                   <root>/per_algorithm/<A|B>/<size>/<capacity>/
    parts = os.path.relpath(race_dir, results_root).split(os.sep)
    if parts[0] == "per_algorithm":
        _, kind, size, capacity = parts[:4]
        return kind, size, int(capacity), {"A": 1000, "B": 5000}.get(kind), None, None
    budget, size, capacity = parts[:3]
    return "all", size, int(capacity), int(budget.split("_")[0]), None, None


DEFAULT_KINDS = ("all", "A", "B")


def collect(results_root, kinds=DEFAULT_KINDS, verbose=True):
    irace = importr("irace")
    rows, skipped = [], []
    rdata_paths = sorted(
        os.path.join(root, f)
        for root, _, files in os.walk(results_root)
        for f in files
        if f == "irace.Rdata"
    )
    for path in rdata_paths:
        race_dir = os.path.dirname(path)
        if ".crashed-" in race_dir:
            skipped.append((path, "archived crash, superseded by a recovery run"))
            continue
        try:
            kind, size, capacity, budget, host, seconds = describe_race(path, results_root)
        except Exception as exc:  # a directory shaped like neither layout
            skipped.append((path, f"unrecognised layout: {exc}"))
            continue
        if kinds and kind not in kinds:
            skipped.append((path, f"kind {kind!r} not requested (--kinds)"))
            continue
        try:
            configs, elites, all_exps, dev = load_race(path, irace)
        except Exception as exc:
            skipped.append((path, str(exc)))
            continue
        for row in summarise_race(configs, elites, all_exps, dev, kind):
            rows.append(
                {
                    "Strategy set": kind,
                    "Instance type": size,
                    "Server capacity": capacity,
                    "Budget": budget,
                    "Host": host,
                    "Race seconds": seconds,
                    **row,
                }
            )
        if verbose:
            print(f"  read {kind}/{size}/{capacity}: {len(elites)} elites", file=sys.stderr)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            by=["Strategy set", "Instance type", "Server capacity", "Elite"],
            key=lambda s: s.map(SIZE_ORDER) if s.name == "Instance type" else s,
        ).reset_index(drop=True)
    return df, skipped


# --- reporting ------------------------------------------------------------------------


def best_elites_dict(df, kind="all"):
    """The cell-4 `best_elites` mapping: winning configuration -> the cells it won.

    Restricted to one strategy set, "all" by default.  The `all` races are the ones the
    final evaluation runs: irace picks the strategy there as well as its parameters.  The
    per-strategy `A` and `B` races answer a different question and cover the *same*
    (instance size, capacity) cells, so folding them in would put two winners in one cell
    -- `A1(SP=HL)` next to the `all` winner for small/1000 -- which silently corrupts both
    `run-alg-parameters.txt.algc` and the elite table, whose cells must be unique.
    """
    winners = df[(df["Elite"] == 1) & (df["Strategy set"] == kind)]
    out = {}
    for _, row in winners.iterrows():
        out.setdefault(row["Configuration"], []).append(
            [row["Instance type"], str(row["Server capacity"])]
        )
    return out


def print_table(df):
    cols = [
        "Strategy set",
        "Instance type",
        "Server capacity",
        "Elite",
        "Configuration",
        "Mean RPD",
        "Mean Rank",
        "Instances run on",
    ]
    with pd.option_context("display.width", 250, "display.max_colwidth", 90):
        print(df[cols].to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="irace_results", help="tree of irace.Rdata")
    ap.add_argument("--out", help="directory for elite_configs.csv / best_elites.json")
    ap.add_argument(
        "--kinds",
        default=",".join(DEFAULT_KINDS),
        help='strategy sets to read (default "all,A,B"); "all" alone restricts the '
        "run to the races the paper's elite table reports",
    )
    args = ap.parse_args()
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())

    print(f"reading {args.results}", file=sys.stderr)
    df, skipped = collect(args.results, kinds)
    if df.empty:
        print("no finished races found", file=sys.stderr)
        return 1

    print_table(df)
    if skipped:
        print("\nskipped:")
        for path, why in skipped:
            print(f"  {path}: {why}")

    print("\nbest_elites (elite 1 of each `all` race, cell-4 format):")
    print(json.dumps(best_elites_dict(df), indent=2))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        csv_path = os.path.join(args.out, "elite_configs.csv")
        json_path = os.path.join(args.out, "best_elites.json")
        df.to_csv(csv_path, index=False)
        with open(json_path, "w") as fh:
            json.dump(best_elites_dict(df), fh, indent=2)
        print(f"\nwrote {csv_path} and {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
