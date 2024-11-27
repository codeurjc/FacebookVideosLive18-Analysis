import pandas as pd
import numpy as np
import os
import ray
from generate_instances_tasks import (
    generate_session,
    generate_instance,
    get_max_duration,
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

if not dont_generate_sessions:
    june_july_dataset_path = "data/June and July dataset/"
    jan_feb_dataset_path = "data/January_February_dataset/"

    def read_data(file):
        return pd.read_json(
            file, lines=True, dtype={"video": "object", "viewers_count": "int32"}
        )

    print("Loading dataset...")
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
    videos_with_high_viewers_total = videos_with_high_viewers_total[
        videos_with_high_viewers_total["video"].isin(video_row_counts.index)
    ]
    videos_with_high_viewers_small = videos_with_high_viewers_small[
        videos_with_high_viewers_small["video"].isin(video_row_counts.index)
    ]
    videos_with_high_viewers_medium = videos_with_high_viewers_medium[
        videos_with_high_viewers_medium["video"].isin(video_row_counts.index)
    ]
    videos_with_high_viewers_big = videos_with_high_viewers_big[
        videos_with_high_viewers_big["video"].isin(video_row_counts.index)
    ]

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

    n_instances_per_type = 40

    max_duration_small = None
    max_duration_medium = None
    max_duration_big = None

    # print("Calculating max durations for instances...")
    # max_duration_small, max_file_small = get_max_duration(small_session_files)
    # print("Max duration small instances:", max_duration_small, max_file_small)
    # max_duration_medium, max_file_med = get_max_duration(medium_session_files, max_duration_small)
    # print("Max duration medium instances:", max_duration_medium, max_file_med)
    # max_duration_big, max_file_big = get_max_duration(big_session_files, max_duration_medium)
    # print("Max duration big instances:", max_duration_big, max_file_big)

    print("Generating instances...")

    for i in range(n_instances_per_type):
        small_instance_size = np.random.randint(10, 21)
        medium_instance_size = np.random.randint(30, 51)
        big_instance_size = int(
            np.ceil(
                0.8
                * (
                    len(small_session_files)
                    + len(medium_session_files)
                    + len(big_session_files)
                )
            )
        )

        small_instance_max_parallel_sessions = np.random.randint(5, 11)
        medium_instance_max_parallel_sessions = np.random.randint(20, 51)
        big_instance_max_parallel_sessions = np.random.randint(100, 201)

        small_session_files_choice = np.random.choice(
            small_session_files, small_instance_size, replace=False
        )
        max_duration_small = get_max_duration.remote(small_session_files_choice)
        small_instance_file = f"instances/instances-small/instance-small-{i}.csv"

        # force 40% of the total instance session size to be from medium sized sessions
        num_medium_files = int(np.ceil(0.4 * medium_instance_size))
        num_other_files = medium_instance_size - num_medium_files
        medium_files_choice = np.random.choice(
            medium_session_files, num_medium_files, replace=False
        )
        other_files_choice = np.random.choice(
            medium_session_files + small_session_files, num_other_files, replace=False
        )
        medium_session_files_choice = np.concatenate(
            (medium_files_choice, other_files_choice)
        )

        max_duration_medium = get_max_duration.remote(medium_session_files_choice)
        medium_instance_file = f"instances/instances-medium/instance-medium-{i}.csv"

        # force 40% of the total instance session size to be from big sized sessions and 20% from medium sized sessions
        num_big_files = int(np.ceil(0.4 * big_instance_size))
        num_medium_files = int(np.ceil(0.2 * big_instance_size))
        num_other_files = big_instance_size - num_big_files - num_medium_files
        big_files_choice = np.random.choice(
            big_session_files, num_big_files
        )  # There aren't enough big session files to choose from to avoid replacement/duplication
        medium_files_choice = np.random.choice(
            medium_session_files, num_medium_files
        )  # Same with medium sessions
        other_files_choice = np.random.choice(
            big_session_files + medium_session_files + small_session_files,
            num_other_files,
            replace=False,
        )
        big_session_files_choice = np.concatenate(
            (big_files_choice, medium_files_choice, other_files_choice)
        )

        max_duration_big = get_max_duration.remote(big_session_files_choice)
        big_instance_file = f"instances/instances-big/instance-big-{i}.csv"

        instance_tasks = []
        instance_tasks.append(
            generate_instance.remote(
                f"small-{i}",
                small_instance_max_parallel_sessions,
                max_duration_small,
                small_session_files_choice,
                small_instance_file,
                session_insertion_method="MAX"
            )
        )
        instance_tasks.append(
            generate_instance.remote(
                f"medium-{i}",
                medium_instance_max_parallel_sessions,
                max_duration_medium,
                medium_session_files_choice,
                medium_instance_file,
                session_insertion_method="MAX"
            )
        )
        instance_tasks.append(
            generate_instance.remote(
                f"big-{i}",
                big_instance_max_parallel_sessions,
                max_duration_big,
                big_session_files_choice,
                big_instance_file,
                session_insertion_method="MAX"
            )
        )

    ray.get(instance_tasks)
    print("Instances generated")
else:
    print("Skipping instance generation")

ray.shutdown()
