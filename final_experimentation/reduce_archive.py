#!/usr/bin/env python3
"""Reduce the STEPS_FULL archives to something that fits on a laptop.

Each run's per-step CSV is streamed straight out of the tar (seek to the member's
byte offset, pipe through `zstd -dc`) and never lands on disk.  Two things come out
of the single pass:

  summary   one row per run: exact maxima and time-weighted depth statistics,
            plus a time-weighted histogram of the depth columns
  series    the same row-index decimation the plotting notebook applies
            (`df.iloc[::sample_rate]`), but with the stride chosen adaptively so
            every run lands at 20k-40k rows whatever its length

Run identity is recovered from the member name, which encodes the strategy's
argument map sorted by CLI key (see AlgorithmStringify.stringifyArgs), and is
rendered as the same `C1(MR=2, RT=SER, ...)` label the run scripts write into the
evaluation logs, so summaries join to the logs on (size, capacity, instance, config).
"""
import gzip
import json
import os
import subprocess
import sys

# --- CSV column positions (StepSaver.java:121) ----------------------------------
C_INSTANCE_EVENT = 0
C_ID = 1
C_TIMESTAMP = 2
C_PLATFORM_AVG_USAGE = 8
C_PLATFORM_TIME_COST = 9
C_N_SERVERS_CURRENT = 10
C_N_SERVERS_TOTAL = 11
C_AVG_VIEWERS = 15
C_AVG_LINKS_FROM = 16
C_AVG_LINKS_TO = 17
C_MAX_TREE_DEPTH = 18
C_AVG_TREE_DEPTH = 19

SERIES_COLUMNS = [
    "timestamp", "server_platform_time_cost", "server_platform_avg_usage",
    "n_servers_current", "avg_links_from", "avg_links_to",
    "sessions", "viewers", "max_tree_depth", "avg_tree_depth",
]

MAX_SERIES_ROWS = 40000   # halved to 20k whenever exceeded, so output is 20k-40k

ABBREV = {
    "HIGH_LOAD": "HL", "LOW_LOAD": "LL", "ROUND_ROBIN": "RR", "RANDOM": "RND",
    "TOP_TO_BOTTOM": "TTB", "BOTTOM_TO_TOP": "BTT", "IGNORE_TREE_LEVEL": "IT",
    "RESERVATION_PER_SERVER": "SER", "RESERVATION_PER_SESSION": "SES",
}


def parse_member_name(name):
    """<instance>-<mode>-<alg>-<args sorted by CLI key>.csv -> run identity."""
    base = name.rsplit(".csv", 1)[0].rsplit(".results", 1)[0]
    p = base.split("-")
    size, instance_id, alg = p[1], int(p[2]), p[4]
    a = ABBREV
    if alg == "A":
        # capacity, seed, viewerServerSelectionOption
        capacity, seed, sp = p[5], p[6], p[7]
        label = f"A1(SP={a[sp]})"
    elif alg == "B":
        # capacity, maxReservation, newViewerServerParentSelectionDistribution,
        # newViewerServerParentSelectionTreeTraversal, seed,
        # viewerServerSelectionDistribution, viewerServerSelectionTreeTraversal
        capacity, cr, omsms, omsdtp, seed, vams, vadtp = p[5:12]
        label = (f"B1(MR={cr}, VAdtp={a[vadtp]}, VAms={a[vams]}, "
                 f"OMSdtp={a[omsdtp]}, OMSms={a[omsms]})")
    elif alg == "C":
        # alreadyLinkedDistribution, alreadyLinkedTreeTraversal, capacity,
        # linkedSpaceDistribution, linkedSpaceTreeTraversal, maxReservation,
        # publisherServerSelectionDistribution, reservationType, seed,
        # viewerAndLinkFromDistribution
        evsp, evst, capacity, ovsp, ovst, cr, psp, rt, seed, nvsp = p[5:15]
        label = (f"C1(MR={cr}, RT={a[rt]}, EVST={a[evst]}, EVSP={a[evsp]}, "
                 f"OVST={a[ovst]}, OVSP={a[ovsp]}, NVSP={a[nvsp]}, PSP={a[psp]})")
    else:
        raise ValueError(f"unknown algorithm {alg!r} in {name}")
    return {
        "instance_type": size, "instance": instance_id, "capacity": int(capacity),
        "algorithm": alg, "config": label, "seed": int(seed), "member": name,
    }


def open_member(tar_path, offset, size):
    """Stream one zstd-compressed member out of the tar without unpacking it."""
    fh = open(tar_path, "rb", buffering=0)
    fh.seek(offset)
    reader = subprocess.Popen(
        ["zstd", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        bufsize=1024 * 1024,
    )

    def pump():
        remaining = size
        while remaining > 0:
            chunk = fh.read(min(1 << 20, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            reader.stdin.write(chunk)
        reader.stdin.close()
        fh.close()

    import threading
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return reader, t


def reduce_run(tar_path, entry, series_dir):
    ident = parse_member_name(entry["name"])
    reader, pump = open_member(tar_path, entry["offset"], entry["size"])

    # exact aggregates over every row
    n_rows = 0
    max_servers = 0
    max_servers_total = 0
    max_sessions = 0
    max_viewers = 0
    max_depth = 0
    max_avg_depth = 0.0
    last_ts = 0.0
    last_cost = 0.0
    # time-weighted accumulators; the weight of a row is the gap to the next row
    tw_total = 0.0
    tw_depth = 0.0
    tw_avg_depth = 0.0
    tw_servers = 0.0
    vw_total = 0.0        # viewer-seconds
    vw_depth = 0.0        # viewer-seconds x max depth
    vw_avg_depth = 0.0    # viewer-seconds x mean depth
    depth_time = {}       # max_tree_depth -> seconds held
    avg_depth_time = {}   # round(avg_tree_depth, 1) -> seconds held

    # session/viewer progression, as simulated.ipynb derives it, but a repeated
    # publisher-leave row (the simulator emits one per cascading server event)
    # only counts once -- guarded by membership in viewers_in_session.
    active_sessions = 0
    active_viewers = 0
    viewers_in_session = {}

    series = []
    stride = 1
    row_in_stride = 0

    prev = None   # (ts, depth, avg_depth, servers, viewers) of the previous row

    stdout = reader.stdout
    header = stdout.readline()
    if not header:
        reader.wait()
        return None

    for line in stdout:
        f = line.rstrip(b"\n").split(b",")
        if len(f) < 20:
            continue
        ev = f[C_INSTANCE_EVENT]
        rid = f[C_ID]
        if ev == b"0":
            active_sessions += 1
            viewers_in_session[rid] = 0
        elif ev == b"2":
            if rid in viewers_in_session:
                active_sessions -= 1
                active_viewers -= viewers_in_session.pop(rid)
        elif ev == b"1":
            active_viewers += 1
            sid = rid.rsplit(b"-", 1)[0]
            viewers_in_session[sid] = viewers_in_session.get(sid, 0) + 1
        elif ev == b"3":
            active_viewers = max(0, active_viewers - 1)
            sid = rid.rsplit(b"-", 1)[0]
            viewers_in_session[sid] = max(0, viewers_in_session.get(sid, 0) - 1)

        ts = float(f[C_TIMESTAMP])
        depth = int(f[C_MAX_TREE_DEPTH])
        avg_depth = float(f[C_AVG_TREE_DEPTH])
        servers = int(f[C_N_SERVERS_CURRENT])

        if prev is not None:
            dt = ts - prev[0]
            if dt > 0:
                p_depth, p_avg, p_servers, p_viewers = prev[1], prev[2], prev[3], prev[4]
                tw_total += dt
                tw_depth += dt * p_depth
                tw_avg_depth += dt * p_avg
                tw_servers += dt * p_servers
                depth_time[p_depth] = depth_time.get(p_depth, 0.0) + dt
                k = round(p_avg, 1)
                avg_depth_time[k] = avg_depth_time.get(k, 0.0) + dt
                vs = dt * p_viewers
                vw_total += vs
                vw_depth += vs * p_depth
                vw_avg_depth += vs * p_avg
        prev = (ts, depth, avg_depth, servers, active_viewers)

        n_rows += 1
        if servers > max_servers:
            max_servers = servers
        if depth > max_depth:
            max_depth = depth
        if avg_depth > max_avg_depth:
            max_avg_depth = avg_depth
        if active_sessions > max_sessions:
            max_sessions = active_sessions
        if active_viewers > max_viewers:
            max_viewers = active_viewers
        last_ts = ts
        last_cost = f[C_PLATFORM_TIME_COST]
        max_servers_total = f[C_N_SERVERS_TOTAL]

        # adaptive row-index decimation
        if row_in_stride == 0:
            series.append((
                f[C_TIMESTAMP], f[C_PLATFORM_TIME_COST], f[C_PLATFORM_AVG_USAGE],
                f[C_N_SERVERS_CURRENT], f[C_AVG_LINKS_FROM], f[C_AVG_LINKS_TO],
                str(active_sessions).encode(), str(active_viewers).encode(),
                f[C_MAX_TREE_DEPTH], f[C_AVG_TREE_DEPTH],
            ))
            if len(series) > MAX_SERIES_ROWS:
                series = series[::2]
                stride *= 2
        row_in_stride = (row_in_stride + 1) % stride

    stdout.close()
    reader.wait()
    pump.join(timeout=5)

    out_name = entry["name"].rsplit(".csv", 1)[0] + ".series.csv.gz"
    with gzip.open(os.path.join(series_dir, out_name), "wb", compresslevel=6) as g:
        g.write((",".join(SERIES_COLUMNS) + "\n").encode())
        for row in series:
            g.write(b",".join(row) + b"\n")

    ident.update({
        "n_rows": n_rows,
        "series_stride": stride,
        "series_rows": len(series),
        "duration": last_ts,
        "objective": float(last_cost),
        "servers_created": int(max_servers_total),
        "max_servers": max_servers,
        "max_sessions": max_sessions,
        "max_viewers": max_viewers,
        "max_tree_depth": max_depth,
        "max_avg_tree_depth": max_avg_depth,
        "tw_seconds": tw_total,
        "tw_mean_max_depth": tw_depth / tw_total if tw_total else 0.0,
        "tw_mean_avg_depth": tw_avg_depth / tw_total if tw_total else 0.0,
        "tw_mean_servers": tw_servers / tw_total if tw_total else 0.0,
        "viewer_seconds": vw_total,
        "vw_mean_max_depth": vw_depth / vw_total if vw_total else 0.0,
        "vw_mean_avg_depth": vw_avg_depth / vw_total if vw_total else 0.0,
        "depth_time_hist": {str(k): round(v, 3) for k, v in sorted(depth_time.items())},
        "avg_depth_time_hist": {str(k): round(v, 3) for k, v in sorted(avg_depth_time.items())},
    })
    return ident


def main():
    tar_path, index_path, series_dir, out_path, shard, nshards = sys.argv[1:7]
    shard, nshards = int(shard), int(nshards)
    os.makedirs(series_dir, exist_ok=True)

    entries = []
    with open(index_path) as fh:
        for i, line in enumerate(fh):
            e = json.loads(line)
            if e["name"].endswith(".csv.zst") and i % nshards == shard:
                entries.append(e)

    with open(out_path, "w") as out:
        for n, e in enumerate(entries, 1):
            try:
                rec = reduce_run(tar_path, e, series_dir)
            except Exception as exc:                      # keep the shard going
                rec = {"member": e["name"], "error": repr(exc)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"[shard {shard}] {n}/{len(entries)} {e['name'][:70]}", file=sys.stderr)


if __name__ == "__main__":
    main()
