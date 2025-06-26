import pandas as pd
import numpy as np
import os
import ray
from generate_instances_tasks import (
    generate_session,
    generate_instance,
    get_max_duration,
    validate_instance_file
)
import glob
import argparse

parser = argparse.ArgumentParser(description="Generate session types and instances.")
parser.add_argument(
    "--no-session-gen", help="Don't generate the session types", action="store_true"
)
parser.add_argument(
    "--no-instance-gen", help="Don't generate the instances", action="store_true"
)
args = parser.parse_args()

dont_generate_sessions = args.no_session_gen
dont_generate_instances = args.no_instance_gen

ray.init()

rng = np.random.default_rng()

if not dont_generate_sessions:
    june_july_dataset_path = "data/June and July dataset/"
    jan_feb_dataset_path = "data/January_February_dataset/"

    def read_data(file):
        return pd.read_json(
            file, lines=True, dtype={"video": "object", "viewers_count": "int32"}
        )

    print("Loading dataset...")
    if os.path.exists("videos_with_high_viewers_small.pkl") and os.path.exists(
        "videos_with_high_viewers_medium.pkl"
    ) and os.path.exists("videos_with_high_viewers_big.pkl"):
        print("Loading saved dataframes...")
        videos_with_high_viewers_small = pd.read_pickle(
            "videos_with_high_viewers_small.pkl"
        )
        videos_with_high_viewers_medium = pd.read_pickle(
            "videos_with_high_viewers_medium.pkl"
        )
        videos_with_high_viewers_big = pd.read_pickle("videos_with_high_viewers_big.pkl")
        print("Loaded saved dataframes")
    else:
        print("Saved dataframes not found. Loading datasets...")
        # Load the data
        viewers_by_fetch_part1 = read_data(
            june_july_dataset_path + "Viewers_by_fetch_part1.json"
        )
        viewers_by_fetch_part2 = read_data(
            june_july_dataset_path + "viewers_by_fetch_part2.json"
        )
        jan_viewers_by_fetch = read_data(
            jan_feb_dataset_path + "viewers_by_fetch_period1and2.json"
        )
        print("Dataset loaded")
        print("Processing datasets before generating sessions...")
        viewers_by_fetch = pd.concat(
            [viewers_by_fetch_part1, viewers_by_fetch_part2, jan_viewers_by_fetch],
            ignore_index=True,
        )
        del viewers_by_fetch_part1, viewers_by_fetch_part2, jan_viewers_by_fetch

        # Convert 'video' column
        viewers_by_fetch["video"] = viewers_by_fetch["video"].apply(
            lambda x: x["$oid"] if isinstance(x, dict) else x
        )

        # Convert 'refresh_time' column to datetime
        viewers_by_fetch["refresh_time"] = pd.to_datetime(viewers_by_fetch["refresh_time"])

        # Optimize memory usage
        viewers_by_fetch["viewers_count"] = pd.to_numeric(
            viewers_by_fetch["viewers_count"], downcast="unsigned"
        )
        viewers_by_fetch["_id"] = viewers_by_fetch["_id"].apply(
            lambda x: x["$oid"] if isinstance(x, dict) else x
        )

        # Get videos with high viewers and separate them into small, medium and big
        min_viewers_threshold = 1000
        max_thresholds = [5000, 10000, float("inf")]
        max_viewers = viewers_by_fetch.groupby("video")["viewers_count"].max()
        videos_with_high_viewers_total = max_viewers[
            max_viewers >= min_viewers_threshold
        ].index
        videos_with_high_viewers_small = max_viewers[
            (max_viewers >= min_viewers_threshold) & (max_viewers < max_thresholds[0])
        ].index
        videos_with_high_viewers_medium = max_viewers[
            (max_viewers >= max_thresholds[0]) & (max_viewers < max_thresholds[1])
        ].index
        videos_with_high_viewers_big = max_viewers[max_viewers >= max_thresholds[1]].index
        videos_with_high_viewers_total = viewers_by_fetch[
            viewers_by_fetch["video"].isin(videos_with_high_viewers_total)
        ]
        videos_with_high_viewers_small = viewers_by_fetch[
            viewers_by_fetch["video"].isin(videos_with_high_viewers_small)
        ]
        videos_with_high_viewers_medium = viewers_by_fetch[
            viewers_by_fetch["video"].isin(videos_with_high_viewers_medium)
        ]
        videos_with_high_viewers_big = viewers_by_fetch[
            viewers_by_fetch["video"].isin(videos_with_high_viewers_big)
        ]

        # Remove videos with less than enough sampling points
        min_sampling_points = 10
        video_row_counts = videos_with_high_viewers_total.groupby(
            "video", observed=True
        ).size()
        video_row_counts = video_row_counts[video_row_counts >= min_sampling_points]
        videos_with_high_viewers_small = videos_with_high_viewers_small[
            videos_with_high_viewers_small["video"].isin(video_row_counts.index)
        ]
        videos_with_high_viewers_medium = videos_with_high_viewers_medium[
            videos_with_high_viewers_medium["video"].isin(video_row_counts.index)
        ]
        videos_with_high_viewers_big = videos_with_high_viewers_big[
            videos_with_high_viewers_big["video"].isin(video_row_counts.index)
        ]

        # dump the dataframes for easier loading next time
        videos_with_high_viewers_small.to_pickle("videos_with_high_viewers_small.pkl")
        videos_with_high_viewers_medium.to_pickle("videos_with_high_viewers_medium.pkl")
        videos_with_high_viewers_big.to_pickle("videos_with_high_viewers_big.pkl")

    print("Generating sessions...")

    os.makedirs("sessions/session-small", exist_ok=True)
    os.makedirs("sessions/session-medium", exist_ok=True)
    os.makedirs("sessions/session-big", exist_ok=True)

    # Generate session types
    session_id_incr = 0
    small_tasks = []
    medium_tasks = []
    big_tasks = []
    for video in videos_with_high_viewers_small["video"].unique():
        video_viewers = videos_with_high_viewers_small[
            videos_with_high_viewers_small["video"] == video
        ][["refresh_time", "viewers_count"]]
        video_viewers = video_viewers.set_index("refresh_time")
        if len(video_viewers) < 2:
            continue
        session_id_incr += 1
        small_tasks.append(
            generate_session.remote(
                f"session-small-{str(session_id_incr)}",
                video_viewers,
                f"sessions/session-small/session-small-{str(session_id_incr)}.csv",
            )
        )

    session_id_incr = 0
    print("Generating small sessions:", len(small_tasks))

    for video in videos_with_high_viewers_medium["video"].unique():
        video_viewers = videos_with_high_viewers_medium[
            videos_with_high_viewers_medium["video"] == video
        ][["refresh_time", "viewers_count"]]
        video_viewers = video_viewers.set_index("refresh_time")
        if len(video_viewers) < 2:
            continue
        session_id_incr += 1
        medium_tasks.append(
            generate_session.remote(
                f"session-medium-{str(session_id_incr)}",
                video_viewers,
                f"sessions/session-medium/session-medium-{str(session_id_incr)}.csv",
            )
        )

    session_id_incr = 0
    print("Generating medium sessions:", len(medium_tasks))

    for video in videos_with_high_viewers_big["video"].unique():
        video_viewers = videos_with_high_viewers_big[
            videos_with_high_viewers_big["video"] == video
        ][["refresh_time", "viewers_count"]]
        video_viewers = video_viewers.set_index("refresh_time")
        if len(video_viewers) < 2:
            continue
        session_id_incr += 1
        big_tasks.append(
            generate_session.remote(
                f"session-big-{str(session_id_incr)}",
                video_viewers,
                f"sessions/session-big/session-big-{str(session_id_incr)}.csv",
            )
        )

    print("Generating big sessions:", len(big_tasks))

    ray.get(small_tasks)
    print("Generated small sessions")
    ray.get(medium_tasks)
    print("Generated medium sessions")
    ray.get(big_tasks)
    print("Generated big sessions")
else:
    print("Skipping session generation")

if not dont_generate_instances:
    print("Starting instance generation...")
    os.makedirs("instances/instances-small", exist_ok=True)
    os.makedirs("instances/instances-medium", exist_ok=True)
    os.makedirs("instances/instances-big", exist_ok=True)

    print("Getting session files...")
    small_session_files = glob.glob("sessions/session-small/*.csv")
    medium_session_files = glob.glob("sessions/session-medium/*.csv")
    big_session_files = glob.glob("sessions/session-big/*.csv")

    max_duration_small = None
    max_duration_medium = None
    max_duration_big = None

    print("Generating instances...")

    n_instances = 6
    small_min_size = 10
    small_max_size = 20
    medium_min_size = 30
    medium_max_size = 50
    
    
    def _batch_generate_validate(gen_tasks, label):
        remaining = gen_tasks.copy()
        validate_tasks = []
        # Launch validation as each generation completes
        while remaining:
            done_refs, _ = ray.wait([t for (_, _, t) in remaining], num_returns=1)
            done_ref = done_refs[0]
            for idx, (i, file_path, gen_ref) in enumerate(remaining):
                if gen_ref == done_ref:
                    v_ref = validate_instance_file.remote(file_path)
                    validate_tasks.append((i, file_path, v_ref))
                    del remaining[idx]
                    break
        # Wait for all validations
        v_refs = [v for (_, _, v) in validate_tasks]
        results = ray.get(v_refs)
        # Collect failures
        next_pending = []
        for (i, file_path, _), valid in zip(validate_tasks, results):
            if not bool(valid):
                print(f"Validation failed for {file_path} in {label}, retrying instance {i}")
                next_pending.append(i)
        return next_pending

    # Generate and validate small instances in batches
    pending = list(range(n_instances))
    while pending:
        gen_tasks = []
        for i in pending:
            file_path = f"instances/instances-small/instance-small-{i}.csv"
            size = rng.integers(small_min_size, small_max_size + 1)
            choice = rng.choice(small_session_files, size, replace=False)
            parallel = rng.integers(5, 11)
            dur_ref = get_max_duration.remote(choice)
            task_ref = generate_instance.remote(
                f"small-{i}", parallel, dur_ref, choice, file_path,
                session_insertion_method="PROPORTIONAL"
            )
            gen_tasks.append((i, file_path, task_ref))
        pending = _batch_generate_validate(gen_tasks, 'small')
 
    # Generate and validate medium instances in batches
    pending = list(range(n_instances))
    while pending:
        gen_tasks = []
        for i in pending:
            file_path = f"instances/instances-medium/instance-medium-{i}.csv"
            small_size = rng.integers(small_min_size, small_max_size + 1)
            med_size = rng.integers(medium_min_size, medium_max_size + 1)
            parallel = rng.integers(20, 51)
            num_med = int(np.ceil(0.4 * med_size))
            num_other = med_size - num_med
            med_ch = rng.choice(medium_session_files, num_med, replace=False)
            oth_ch = rng.choice(medium_session_files + small_session_files, num_other, replace=False)
            files = np.concatenate((med_ch, oth_ch))
            dur_ref = get_max_duration.remote(files)
            task_ref = generate_instance.remote(
                f"medium-{i}", parallel, dur_ref, files, file_path,
                session_insertion_method="PROPORTIONAL"
            )
            gen_tasks.append((i, file_path, task_ref))
        pending = _batch_generate_validate(gen_tasks, 'medium')
 
    # Generate and validate big instances in batches
    pending = list(range(n_instances))
    total_sessions = len(small_session_files) + len(medium_session_files) + len(big_session_files)
    big_size = int(np.ceil(0.8 * total_sessions))
    while pending:
        gen_tasks = []
        for i in pending:
            file_path = f"instances/instances-big/instance-big-{i}.csv"
            parallel = rng.integers(20, 41)
            num_big = int(np.ceil(0.4 * big_size))
            num_med = int(np.ceil(0.2 * big_size))
            num_other = big_size - num_big - num_med
            big_ch = rng.choice(big_session_files, num_big)
            med_ch = rng.choice(medium_session_files, num_med)
            oth_ch = rng.choice(big_session_files + medium_session_files + small_session_files, num_other, replace=False)
            files = np.concatenate((big_ch, med_ch, oth_ch))
            dur_ref = get_max_duration.remote(files)
            task_ref = generate_instance.remote(
                f"big-{i}", parallel, dur_ref, files, file_path,
                session_insertion_method="PROPORTIONAL"
            )
            gen_tasks.append((i, file_path, task_ref))
        pending = _batch_generate_validate(gen_tasks, 'big')

    print("Instances generated")
else:
    print("Skipping instance generation")

ray.shutdown()
