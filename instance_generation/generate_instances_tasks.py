import ray
import pandas as pd
import numpy as np


def join_viewer(timestamp, viewer_ids, viewer_id):
    viewer_ids.append(viewer_id)
    return [1, viewer_id, timestamp]


def leave_viewer(timestamp, viewer_ids):
    viewer_id = viewer_ids.pop(np.random.randint(len(viewer_ids)))
    return [3, viewer_id, timestamp]


def progress_viewers(
    start_viewer_count,
    end_viewer_count,
    start_timestamp,
    end_timestamp,
    viewer_ids,
    starting_viewer_id,
):
    timediff = end_timestamp - start_timestamp
    viewer_id_incr = starting_viewer_id
    rows = []
    if end_viewer_count > start_viewer_count:
        new_viewers = end_viewer_count - start_viewer_count
        leavers_size = int(
            end_viewer_count * 0.1
        )  # 10% of total viewers leave at random points and others new join
        full_size = new_viewers + leavers_size
        random_points = np.random.choice(
            range(1, full_size), size=leavers_size, replace=False
        )  # last viewer omitted to avoid weird cases
        time_stops = timediff / full_size
        for i in range(1, full_size + 1):
            rows.append(
                join_viewer(
                    start_timestamp + i * time_stops, viewer_ids, viewer_id_incr
                )
            )
            viewer_id_incr += 1
            if i in random_points:
                rows.append(
                    leave_viewer(start_timestamp + (i + 0.5) * time_stops, viewer_ids)
                )
    elif start_viewer_count > end_viewer_count:
        gone_viewers = start_viewer_count - end_viewer_count
        joiners_size = int(
            end_viewer_count * 0.1
        )  # 10% of total viewers join at random points while others leave
        full_size = gone_viewers + joiners_size
        random_points = np.random.choice(
            range(1, full_size), size=joiners_size, replace=False
        )  # last viewer omitted to avoid weird cases
        time_stops = timediff / full_size
        for i in range(1, full_size + 1):
            rows.append(leave_viewer(start_timestamp + i * time_stops, viewer_ids))
            if i in random_points:
                rows.append(
                    join_viewer(
                        start_timestamp + (i + 0.5) * time_stops,
                        viewer_ids,
                        viewer_id_incr,
                    )
                )
                viewer_id_incr += 1
    else:
        joiners_leavers_size = int(
            start_viewer_count * 0.1
        )  # 10% of total viewers leave and join at random points
        full_size = start_viewer_count + joiners_leavers_size
        random_joiner_points = np.random.choice(
            range(1, full_size), size=joiners_leavers_size, replace=False
        )  # last viewer omitted to avoid weird cases
        random_leaver_points = np.random.choice(
            range(1, full_size), size=joiners_leavers_size, replace=False
        )
        time_stops = timediff / full_size
        for i in range(1, full_size + 1):
            if i in random_leaver_points:
                rows.append(leave_viewer(start_timestamp + i * time_stops, viewer_ids))
            if i in random_joiner_points:
                rows.append(
                    join_viewer(
                        start_timestamp + i * time_stops, viewer_ids, viewer_id_incr
                    )
                )
                viewer_id_incr += 1
    return viewer_id_incr, rows


@ray.remote
def generate_session(session_id, video_viewers, filepath):
    prev_viewers = 0
    prev_timestamp = None
    viewer_id_incr = 0
    viewer_ids = []
    rows = {"event": [], "id": [], "timestamp": []}

    def add_event(event, id, timestamp):
        rows["event"].append(event)
        rows["id"].append(id)
        rows["timestamp"].append(timestamp)

    def extend_events(events):
        for event in events:
            add_event(event[0], event[1], event[2])

    # take the period between the first 2 points of data
    first_end_timestamp = video_viewers.index[0]
    second_end_timestamp = video_viewers.index[1]
    # take diff between 2nd and 1st timestamp and subtract it from 1st timestamp to get a simulated start timestamp
    timediff = second_end_timestamp - first_end_timestamp
    first_end_timestamp = timediff.total_seconds()
    add_event(0, session_id, 0)
    viewer_id_incr, p_rows = progress_viewers(
        0,
        video_viewers.iloc[0]["viewers_count"],
        0,
        first_end_timestamp,
        viewer_ids,
        viewer_id_incr,
    )
    extend_events(p_rows)
    prev_viewers = video_viewers.iloc[0]["viewers_count"]
    prev_timestamp = first_end_timestamp
    for i in range(1, len(video_viewers)):
        next_end_timestamp = (
            video_viewers.index[i] - video_viewers.index[i - 1]
        ).total_seconds() + prev_timestamp
        next_viewers = video_viewers.iloc[i]["viewers_count"]
        viewer_id_incr, p_rows = progress_viewers(
            prev_viewers,
            next_viewers,
            prev_timestamp,
            next_end_timestamp,
            viewer_ids,
            viewer_id_incr,
        )
        extend_events(p_rows)
        prev_viewers = next_viewers
        prev_timestamp = next_end_timestamp

    add_event(2, session_id, prev_timestamp)
    df = pd.DataFrame(rows)
    df["id"] = df["id"].apply(
        lambda x: f"{session_id}-{x}" if x != session_id else x
    )  # add session id to viewers ids for better identification
    df.to_csv(filepath, index=False)


@ray.remote
def get_max_duration(files, prev_max=None):
    max_duration = prev_max
    for file in files:
        with open(file, "r") as f:
            lines = f.readlines()
            session_starts = lines[1].strip().split(",")[2]
            session_ends = lines[-1].strip().split(",")[2]
            timediff = float(session_ends) - float(session_starts)
            if max_duration is None or timediff > max_duration:
                max_duration = timediff
    return max_duration

def set_categorical_event(df):
    # Define the custom sorting order for the "event" column
    event_order = pd.CategoricalDtype(categories=[0, 1, 3, 2], ordered=True)
    # Convert the "event" column to the custom categorical type
    df["event"] = df["event"].astype(event_order)

@ray.remote
def generate_instance(instance_number, max_parallel_sessions, stop_time, session_files, instance_file, session_insertion_method="ACTIVE"):
    max_time = stop_time * 3  # 3 times the duration of the longest session
    print(
        f"Generating instance {instance_number} with {len(session_files)} session types, max parallel sessions: {max_parallel_sessions}, soft max duration: {max_time}"
    )
    current_time = 0
    instance_rows = {"event": [], "id": [], "timestamp": [], "repeat": []}

    session_idx = np.random.randint(
        len(session_files)
    )  # first session is a random session
    used_sessions = set()
    session = session_files[session_idx]
    session_df = pd.read_csv(session)
    set_categorical_event(session_df)
    session_df["session"] = session_idx
    session_df["repeat"] = 0
    current_repeat = 0
    active_sessions = 1
    if session_insertion_method == "ACTIVE":
        time_wait_sessions = [
            session_df["timestamp"].iloc[-1]
        ]  # queue of sessions to wait for, will always use the first element for calculating time to wait
    elif session_insertion_method == "MAX":
        time_wait_sessions = [stop_time]
    # between 5% and 10% of sessions' duration is the time to wait for the next session to start
    time_wait_for_next_session_chance = np.random.uniform(1, 5) / 100
    time_wait_for_next_session = (
        time_wait_sessions[0] * time_wait_for_next_session_chance
    )

    # while we have not reached the end of the simulation time add events
    while current_time <= max_time:
        actual_time = session_df.iloc[0]["timestamp"]
        # add events until there are none or we reach the time to wait for the next session
        while not session_df.empty and actual_time <= time_wait_for_next_session:
            instance_rows["event"].append(session_df.iloc[0]["event"])
            instance_rows["id"].append(session_df.iloc[0]["id"])
            instance_rows["timestamp"].append(session_df.iloc[0]["timestamp"])
            actual_time = session_df.iloc[0]["timestamp"]
            session = session_df.iloc[0]["session"]
            repeat = session_df.iloc[0]["repeat"]
            instance_rows["repeat"].append(repeat) # TODO: this adds size to the instance file size, but it is useful for the evaluation, so it should be a script argument
            session_df = session_df.iloc[1:]
            # if a session is over it is removed from the active sessions
            if (
                session_df.empty
                or session not in session_df["session"].values
                or repeat not in session_df["repeat"].values
            ):
                active_sessions -= 1
                if session_insertion_method == "ACTIVE":
                    time_wait_sessions.remove(actual_time)
        if session_df.empty:
            # if the session no longer exists, we need to wait for the next session
            current_time = time_wait_for_next_session
        else:
            current_time = actual_time

        # if we can add a new session, we add it
        if active_sessions < max_parallel_sessions:
            # previous session is already used
            used_sessions.add(session_idx)
            if len(used_sessions) == len(session_files):
                # if there are no more sessions available we can start using previous ones (marked)
                current_repeat += 1
                used_sessions = set()
            while session_idx in used_sessions:
                # get a new unused session
                session_idx = np.random.randint(len(session_files))
            new_session = session_files[session_idx]
            new_session_df = pd.read_csv(new_session)
            set_categorical_event(new_session_df)
            new_session_df["timestamp"] = new_session_df["timestamp"] + current_time
            new_session_df["session"] = session_idx
            new_session_df["repeat"] = current_repeat
            if session_insertion_method == "ACTIVE":
                time_wait_sessions.append(new_session_df["timestamp"].iloc[-1])
            session_df = pd.concat([session_df, new_session_df]).sort_values(
                by=["timestamp", "event"]
            )
            active_sessions += 1

        # get a new the time to wait for the next session
        time_wait_for_next_session_chance = np.random.uniform(5, 10) / 100
        time_wait_for_next_session = (
            time_wait_sessions[0] * time_wait_for_next_session_chance + current_time
        )

    # finish existing sessions that are not finished
    while not session_df.empty:
        instance_rows["event"].append(session_df.iloc[0]["event"])
        instance_rows["id"].append(session_df.iloc[0]["id"])
        instance_rows["timestamp"].append(session_df.iloc[0]["timestamp"])
        instance_rows["repeat"].append(session_df.iloc[0]["repeat"])
        session_df = session_df.iloc[1:]

    final_df = pd.DataFrame(instance_rows)
    final_df.to_csv(instance_file, index=False)
    print(f"Instance {instance_number} generated")
