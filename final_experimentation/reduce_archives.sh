#!/bin/bash
# Reduce the STEPS_FULL archives written by llls-simulator/run-alg-full-on-all.sh.
#
# The archives are far too large to unpack (the Strategy C grid alone is 457 GB), so each
# run's per-step CSV is streamed out of the tar by byte offset, decompressed on the fly and
# thrown away once its statistics have been taken.  Nothing is written to disk except the
# reduced output, which is about 0.2% of the input:
#
#   $OUT/summary/<archive>.jsonl   one JSON record per run: exact maxima, time- and
#                                  viewer-weighted depth statistics, depth histograms
#   $OUT/series/<archive>/*.csv.gz one decimated per-step series per run (20k-40k rows)
#
# Usage:
#   ./reduce_archives.sh <results-dir> <out-dir> [archive ...]
#
#   ./reduce_archives.sh /mnt/instances/llls-results steps_full small medium big
#   ./reduce_archives.sh /mnt/instances/llls-results steps_full a_small b_big
#
# NSHARDS parallel workers (default 48); each holds one decompression pipe, so set it to
# roughly the core count.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${1:?usage: reduce_archives.sh <results-dir> <out-dir> [archive ...]}"
OUT="${2:?usage: reduce_archives.sh <results-dir> <out-dir> [archive ...]}"
shift 2
NSHARDS="${NSHARDS:-48}"

mkdir -p "$OUT"/{index,series,summary,logs} || exit 1

for name in "$@"; do
    tar="$RESULTS_DIR/$name.tar"
    [[ -f "$tar" ]] || { echo "[reduce] no such archive: $tar" >&2; continue; }

    if [[ ! -s "$OUT/index/$name.jsonl" ]]; then
        echo "[reduce] indexing $name" >&2
        python3 "$HERE/tar_index.py" "$tar" "$OUT/index/$name.jsonl" \
            > "$OUT/logs/index-$name.log" 2>&1 \
            || { echo "[reduce] index $name FAILED" >&2; continue; }
    fi

    runs=$(grep -c 'csv.zst' "$OUT/index/$name.jsonl")
    echo "[reduce] $name: $runs runs, $NSHARDS shards" >&2
    mkdir -p "$OUT/series/$name" "$OUT/summary/$name"
    start=$SECONDS
    for s in $(seq 0 $((NSHARDS - 1))); do
        python3 "$HERE/reduce_archive.py" "$tar" "$OUT/index/$name.jsonl" \
            "$OUT/series/$name" "$OUT/summary/$name/shard-$s.jsonl" "$s" "$NSHARDS" \
            > /dev/null 2> "$OUT/logs/reduce-$name-$s.log" &
    done
    wait
    cat "$OUT/summary/$name"/shard-*.jsonl > "$OUT/summary/$name.jsonl"
    echo "[reduce] $name done in $((SECONDS - start))s: $(wc -l < "$OUT/summary/$name.jsonl") runs, errors=$(grep -c '"error"' "$OUT/summary/$name.jsonl")" >&2
done
echo "[reduce] finished" >&2
