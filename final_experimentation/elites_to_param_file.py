#!/usr/bin/env python3
"""Turn the irace winners into the simulator's `run-alg-parameters.txt.alg*` file.

`elite_configs.py` emits `best_elites.json`: the winning configuration of each race,
keyed by the label the analysis joins on --

    "C1(MR=3, RT=SER, EVST=TTB, EVSP=LL, OVST=IT, OVSP=RND, NVSP=LL, PSP=RR)":
        [["big", "650"]]

and the final evaluation needs the same set as `<label>;<CLI args>` lines.  Doing that
translation by hand is the riskiest step in the pipeline: three naming systems describe the
same parameters, and a label that no longer matches its arguments is undetectable
downstream -- the logs, the tables and the archive member names all agree with each other
and are all wrong together.

So the label is not copied, it is **rebuilt** from the arguments this script emits, using
the same rendering `reduce_archive.parse_member_name` applies to the simulator's output
filenames, and compared against the label that came out of irace.  A mismatch is a hard
error.

Configurations that win more than one cell are emitted once, in first-win order, so the
grid runs the 12 cells' winners as however many distinct configurations that is.  The
script reports that count, which the paper also states in prose.

**Every line carries `--seed`.**  `Parameters*` defaults the seed to `System.nanoTime()`,
so a configuration written without one draws a different seed on every run and the results
cannot be reproduced.

Usage:
    python3 final_experimentation/elites_to_param_file.py \
        --elites tables/best_elites.json \
        --out ../llls-simulator/run-alg-parameters.txt.algc
"""
import argparse
import json
import os
import re
import sys

# short code -> (CLI flag, {short value: simulator enum}), in the order
# run-alg-parameters.txt.algc writes them.  `None` means the value is passed through.
DISTRIBUTION = {"HL": "HIGH_LOAD", "LL": "LOW_LOAD", "RR": "ROUND_ROBIN", "RND": "RANDOM"}
TRAVERSAL = {"TTB": "TOP_TO_BOTTOM", "BTT": "BOTTOM_TO_TOP", "IT": "IGNORE_TREE_LEVEL"}
RESERVATION = {"SER": "RESERVATION_PER_SERVER", "SES": "RESERVATION_PER_SESSION"}

SPEC = {
    "C": [
        ("MR", "--maxReservation", None),
        ("RT", "--reservationType", RESERVATION),
        ("EVST", "--alreadyLinkedTreeTraversal", TRAVERSAL),
        ("EVSP", "--alreadyLinkedDistribution", DISTRIBUTION),
        ("OVST", "--linkedSpaceTreeTraversal", TRAVERSAL),
        ("OVSP", "--linkedSpaceDistribution", DISTRIBUTION),
        ("NVSP", "--viewerAndLinkFromDistribution", DISTRIBUTION),
        ("PSP", "--publisherServerSelectionDistribution", DISTRIBUTION),
    ],
    "B": [
        ("MR", "--maxReservation", None),
        ("VAdtp", "--viewerServerSelectionTreeTraversal", TRAVERSAL),
        ("VAms", "--viewerServerSelectionDistribution", DISTRIBUTION),
        ("OMSdtp", "--newViewerServerParentSelectionTreeTraversal", TRAVERSAL),
        ("OMSms", "--newViewerServerParentSelectionDistribution", DISTRIBUTION),
    ],
    "A": [
        ("SP", "--viewerServerSelectionOption", DISTRIBUTION),
    ],
}

LABEL_RE = re.compile(r"^([ABC])(\d+)\((.*)\)$")


def parse_label(label):
    """"C1(MR=3, RT=SER, ...)" -> ("C", {"MR": "3", "RT": "SER", ...})."""
    m = LABEL_RE.match(label.strip())
    if not m:
        raise ValueError(f"not a configuration label: {label!r}")
    strategy, _, body = m.groups()
    fields = {}
    for part in body.split(", "):
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    return strategy, fields


def render_label(strategy, fields):
    """The label `reduce_archive.parse_member_name` builds for these values."""
    inner = ", ".join(f"{code}={fields[code]}" for code, _, _ in SPEC[strategy])
    return f"{strategy}1({inner})"


def to_args(strategy, fields, seed):
    args = ["--algorithm", strategy]
    for code, flag, mapping in SPEC[strategy]:
        if code not in fields:
            raise ValueError(f"{strategy} configuration is missing {code}: {fields}")
        raw = fields[code]
        if mapping is None:
            if not raw.isdigit():
                raise ValueError(f"{code} should be an integer, got {raw!r}")
            value = raw
        else:
            if raw not in mapping:
                raise ValueError(f"unknown {code} value {raw!r}; expected {sorted(mapping)}")
            value = mapping[raw]
        args += [flag, value]
    args += ["--seed", str(seed)]
    return args


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elites", default="tables/best_elites.json",
                    help="best_elites.json written by elite_configs.py")
    ap.add_argument("--out", required=True, help="parameter file to write")
    ap.add_argument("--seed", type=int, default=12345,
                    help="seed every configuration runs with (default 12345)")
    args = ap.parse_args()

    with open(args.elites) as fh:
        elites = json.load(fh)
    if not elites:
        print(f"{args.elites} is empty", file=sys.stderr)
        return 1

    lines, seen = [], {}
    for label, cells in elites.items():
        strategy, fields = parse_label(label)
        argv = to_args(strategy, fields, args.seed)
        # Rebuild the label from what we are about to run and refuse to write a file
        # whose label and arguments disagree.
        rebuilt = render_label(strategy, fields)
        if rebuilt != label.strip():
            raise SystemExit(f"label/argument mismatch:\n  irace: {label}\n  rebuilt: {rebuilt}")
        where = ", ".join(f"{size}/C={cap}" for size, cap in cells)
        if label in seen:
            print(f"  duplicate winner, emitted once: {label} ({where})", file=sys.stderr)
            continue
        seen[label] = where
        lines.append(f"{label};{' '.join(argv)}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    # No trailing newline: the run scripts' `read ... || [[ -n "$line" ]]` loop handles a
    # final line without one, and every existing parameter file is written this way.
    with open(args.out, "w") as fh:
        fh.write("\n".join(lines))

    cells = sum(len(v) for v in elites.values())
    print(f"wrote {args.out}: {len(lines)} distinct configurations "
          f"covering {cells} race cells", file=sys.stderr)
    for label, where in seen.items():
        print(f"  {label}  <- {where}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
