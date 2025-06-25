import ray
import pandas as pd
import numpy as np
import instance_generation.instance_validator as validator

rng = np.random.default_rng()

def progress_viewers(
    start_viewer_count,
    end_viewer_count,
    start_timestamp,
    end_timestamp,
    viewer_ids,
    starting_viewer_id,
):
    n = abs(end_viewer_count - start_viewer_count) # viewer changes
    if n == 0:
        return starting_viewer_id, []
    total_steps = n * 2 # increase constant to add more steps for smoother randomness if needed

    rows = []
    viewer_id_incr = starting_viewer_id + 1
    current_viewers = start_viewer_count

    max_diff = max(start_viewer_count, end_viewer_count)
    for _ in range(total_steps):
        viewer_diff = abs(current_viewers - end_viewer_count)
        bias_strength = viewer_diff / max_diff # adjust bias based on viewer count difference
        base_bias = 0.5
        if current_viewers < end_viewer_count:
            event = 1 if rng.random() < (base_bias + bias_strength) else 3
        else:
            event = 3 if rng.random() < (base_bias + bias_strength) else 1

        if event == 1:
            viewer_id = viewer_id_incr
            viewer_ids.add(viewer_id)
            viewer_id_incr += 1
            current_viewers += 1
        elif event == 3 and len(viewer_ids) > 0:
            viewer_id = rng.choice(list(viewer_ids))
            viewer_ids.remove(viewer_id)
            current_viewers -= 1
        else:
            continue
        rows.append([event, viewer_id])

    # Ensure total matches end_viewer_count
    while current_viewers < end_viewer_count:
        viewer_id = viewer_id_incr
        viewer_id_incr += 1
        viewer_ids.add(viewer_id)
        rows.append([1, viewer_id])
        current_viewers += 1

    while current_viewers > end_viewer_count:
        viewer_id = rng.choice(list(viewer_ids))
        rows.append([3, viewer_id])
        viewer_ids.remove(viewer_id)
        current_viewers -= 1

    # Distribute the event types over time
    total_events = len(rows)
    timestamps = np.linspace(start_timestamp + (end_timestamp - start_timestamp) / total_events, end_timestamp, total_events)

    rows = [[rows[i][0], rows[i][1], timestamps[i]] for i in range(total_events)]

    # Optional: add end mark
    # TODO: this should probably be an argument
    for i in range(len(rows) - 1):
        rows[i].append(0)
    rows[-1].append(1)

    return viewer_id_incr, rows


@ray.remote
def generate_session(session_id, video_viewers, filepath):
    print(f"Generating session {session_id} with {len(video_viewers)} points of data")
    prev_viewers = 0
    prev_timestamp = 0.0
    viewer_id_incr = 0
    viewer_ids = set()
    rows = {"event": [], "id": [], "timestamp": [], "end_mark": []}


    def add_event(event, id, timestamp, end_mark):
        rows["event"].append(event)
        rows["id"].append(id)
        rows["timestamp"].append(timestamp)
        rows["end_mark"].append(end_mark)

    def extend_events(events):
        for event in events:
            add_event(event[0], event[1], event[2], event[3])

    # take the period between the first 2 points of data
    first_end_timestamp = video_viewers.index[0]
    second_end_timestamp = video_viewers.index[1]
    # take diff between 2nd and 1st timestamp and subtract it from 1st timestamp to get a simulated start timestamp
    timediff = (second_end_timestamp - first_end_timestamp).total_seconds()
    timediff = rng.uniform(timediff / 2, timediff)
    first_end_timestamp = float(timediff)
    video_viewers.index = (video_viewers.index - video_viewers.index.min()).total_seconds() + first_end_timestamp
    add_event(0, session_id, 0, 0)
    viewer_id_incr, p_rows = progress_viewers(
        0,
        int(video_viewers.iloc[0]["viewers_count"]),
        0,
        first_end_timestamp,
        viewer_ids,
        viewer_id_incr,
    )
    extend_events(p_rows)
    prev_viewers = video_viewers.iloc[0]["viewers_count"]
    prev_timestamp = first_end_timestamp
    for i in range(1, len(video_viewers)):
        next_end_timestamp = video_viewers.index[i]
        next_viewers = int(video_viewers.iloc[i]["viewers_count"])
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

    add_event(2, session_id, prev_timestamp, 0)
    df = pd.DataFrame(rows)
    df["id"] = df["id"].apply(
        lambda x: f"{session_id}-{x}" if x != session_id else x
    )  # add session id to viewers ids for better identification
    df.to_csv(filepath, index=False)
    print(f"Session {session_id} generated")


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
def generate_instance(instance_number, max_parallel_sessions_max, stop_time, session_files, instance_file, session_insertion_method="ACTIVE"):
    max_time = stop_time * 3  # 3 times the duration of the longest session
    current_time = 0
    session_files_list = list(session_files.copy())
    session_idx = rng.integers(len(session_files_list))  # first session is a random session
    session = session_files_list.pop(session_idx)
    session_df = pd.read_csv(session)
    set_categorical_event(session_df)
    session_df["repeat"] = 0
    repeats_map = {
        session: 0
    }
    active_sessions = 1
    max_parallel_sessions_lower_bound = int(max_parallel_sessions_max / 2)
    max_parallel_sessions = rng.integers(max_parallel_sessions_lower_bound, max_parallel_sessions_max + 1)
    print(
        f"Instance {instance_number}: Generating instance {instance_number} with {len(session_files)} session types, max parallel sessions range: {max_parallel_sessions_lower_bound}-{max_parallel_sessions_max}, soft max duration: {max_time}"
    )
    print(f"Max parallel sessions: {max_parallel_sessions}")
    if session_insertion_method == "ACTIVE":
        time_wait_sessions = [
            session_df["timestamp"].iloc[-1]
        ]  # queue of sessions to wait for, will always use the first element for calculating time to wait
        # between 1% and 5% of sessions' duration is the time to wait for the next session to start
        time_wait_for_next_session_chance = rng.uniform(5, 10) / 100
        time_wait_for_next_session = (
            time_wait_sessions[0] * time_wait_for_next_session_chance
        )
    elif session_insertion_method == "MAX":
        time_wait_sessions = [stop_time]
        # between 1% and 5% of sessions' duration is the time to wait for the next session to start
        time_wait_for_next_session_chance = rng.uniform(5, 10) / 100
        time_wait_for_next_session = (
            time_wait_sessions[0] * time_wait_for_next_session_chance
        )
    elif session_insertion_method == "PROPORTIONAL":
        n_sessions = max_time / (max_parallel_sessions_max ** 2)
        print(f"Instance {instance_number}: Proportional time range between sessions: {n_sessions / 2} - {n_sessions}")
        time_wait_for_next_session = rng.uniform(n_sessions / 2, n_sessions)

    buffer = []
    buffer_size = 100000  # Adjust buffer size as needed

    with open(instance_file, 'w') as f:
        f.write("event,id,timestamp,repeat\n")
        # while we have not reached the end of the simulation time add events
        while current_time <= max_time:
            print(f"Instance {instance_number}: Active sessions: {active_sessions}")
            actual_time = session_df.iloc[0]["timestamp"]
            # add events until there are none or we reach the time to wait for the next session
            print(f"Instance {instance_number}: Next session time: {time_wait_for_next_session}")
            while not session_df.empty and actual_time <= time_wait_for_next_session:
                first_row = session_df.iloc[0]
                event = first_row["event"]
                buffer.append(f"{event},{first_row['id']},{first_row['timestamp']},{first_row['repeat']}\n")
                if len(buffer) >= buffer_size:
                    f.writelines(buffer)
                    buffer = []
                actual_time = first_row["timestamp"]
                repeat = first_row["repeat"]
                session_df = session_df.iloc[1:]
                # if a session is over it is removed from the active sessions
                if event == 2:
                    active_sessions -= 1
                    print(f"Instance {instance_number}: Session {first_row['id']} repeat {repeat} finished, active sessions: {active_sessions}")
                    if session_insertion_method == "ACTIVE":
                        time_wait_sessions.remove(actual_time)
            if session_df.empty:
                print(f"Instance {instance_number}: No more events in the current session group")
                # if the session no longer exists, we need to wait for the next session
                current_time = time_wait_for_next_session
            else:
                current_time = actual_time

            # if we can add a new session, we add it
            if active_sessions < max_parallel_sessions:
                print(f"Instance {instance_number}: Adding new session at time {current_time}")
                if len(session_files_list) == 0:
                    # if there are no more sessions available we can start using previous ones (marked)
                    session_files_list = list(session_files.copy())
                    print(f"Instance {instance_number}: All sessions used, repeating sessions")
                # get a new unused session
                session_idx = rng.integers(len(session_files_list))
                new_session = session_files_list.pop(session_idx)
                if new_session in repeats_map:
                    repeats_map[new_session] += 1
                else:
                    repeats_map[new_session] = 0
                new_session_df = pd.read_csv(new_session)
                set_categorical_event(new_session_df)
                new_session_df["timestamp"] = new_session_df["timestamp"] + current_time
                new_session_df["repeat"] = repeats_map[new_session]
                if session_insertion_method == "ACTIVE":
                    time_wait_sessions.append(new_session_df["timestamp"].iloc[-1])
                session_df = pd.concat([session_df, new_session_df]).sort_values(
                    by=["timestamp", "event"]
                )
                active_sessions += 1

            max_parallel_sessions = rng.integers(max_parallel_sessions_lower_bound, max_parallel_sessions_max + 1)
            print(f"New max parallel sessions: {max_parallel_sessions}")
            # get a new the time to wait for the next session
            if session_insertion_method == "PROPORTIONAL":
                time_wait_for_next_session = rng.uniform(n_sessions / 2, n_sessions) + current_time
            else:
                time_wait_for_next_session_chance = rng.uniform(5, 10) / 100
                time_wait_for_next_session = (
                    time_wait_sessions[0] * time_wait_for_next_session_chance + current_time
                )
        print(f"Instance {instance_number}: End of simulation time reached")
        # finish existing sessions that are not finished
        while not session_df.empty:
            buffer.append(f"{session_df.iloc[0]['event']},{session_df.iloc[0]['id']},{session_df.iloc[0]['timestamp']},{session_df.iloc[0]['repeat']}\n")
            session_df = session_df.iloc[1:]
        if buffer:
            f.writelines(buffer)
    print(f"Instance {instance_number}: Instance {instance_number} generated")

@ray.remote
def validate_instance(instance_file):
    validator.validate_instance(instance_file)