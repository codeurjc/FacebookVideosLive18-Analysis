#!/usr/bin/env bash
# Pull finished irace races from the tuning machines into irace_results/.
#
# The grid coordinator is `biginstance`: every worker rsyncs a race to
# /mnt/instances/tuning-results on it as soon as that race finishes, so the coordinator
# is normally a superset of the workers.  "Normally" is doing work there -- a worker's
# post-race sync can fail, and the worker then keeps the only copy -- so this pulls the
# workers too and lets rsync skip what already matches.
#
# In-progress races are excluded: a running race's irace.Rdata is an iteration
# checkpoint with no allElites and no testing block, so the analysis cannot read it and
# copying a half-written one only invites confusion.
#
# Usage:  scripts/pull-tuning-results.sh [destination]     (default irace_results)
set -euo pipefail

DEST="${1:-irace_results}"
COORDINATOR="biginstance:/mnt/instances/tuning-results/"
WORKERS=("biginstance2:/home/ubuntu/tuning-results/")

mkdir -p "$DEST"

pull() {
    local src="$1"
    echo "==> $src"
    # --ignore-existing is deliberately NOT used: a race directory can gain its
    # irace.Rdata after its irace.log was already pulled.
    rsync -az --info=stats2 \
        --exclude='irace.log.recovered' \
        --exclude='*.partial' \
        -e "ssh -o BatchMode=yes -o ConnectTimeout=15" \
        "$src" "$DEST/" 2>&1 | sed -n 's/^Number of regular files transferred: /    files transferred: /p'
}

pull "$COORDINATOR"
for w in "${WORKERS[@]}"; do
    pull "$w" || echo "    (unreachable, skipped)"
done

echo
echo "finished races now in $DEST:"
find "$DEST" -name irace.Rdata -not -path '*.crashed-*' -printf '    %h\n' | sort

echo
echo "run:  python3 final_experimentation/elite_configs.py --results $DEST --out tables"
