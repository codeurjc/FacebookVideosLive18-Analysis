def validate_instance(instance_file):
    seen = {}
    with open(instance_file, 'r', encoding='utf-8') as f:
        header = f.readline().strip().split(',')
        id_idx = header.index("id")
        repeat_idx = header.index("repeat")
        event_idx = header.index("event")
        for idx, line in enumerate(f):
            row = line.strip().split(',')
            row_id = row[id_idx]
            row_repeat = row[repeat_idx]
            event = int(row[event_idx])
            key = (row_id, row_repeat)
            if key not in seen and (event == 2 or event == 3):
                print(
                    f"Warning: In file '{instance_file}', for id={row_id} and repeat={row_repeat}, expected event 0 or 1 but got event={event} at row {idx} (CSV row {idx + 2})"
                )
                return False
            else:
                seen[key] = event
    return True

def validate_session(session_file):
    seen = {}
    with open(session_file, 'r', encoding='utf-8') as f:
        header = f.readline().strip().split(',')
        id_idx = header.index("id")
        event_idx = header.index("event")
        for idx, line in enumerate(f):
            row = line.strip().split(',')
            row_id = row[id_idx]
            event = int(row[event_idx])
            if row_id not in seen and (event == 2 or event == 3):
                print(
                    f"Warning: In file '{session_file}', for id={row_id}, expected event 0 or 1 but got event={event} at row {idx} (CSV row {idx + 2})"
                )
                return False
            else:
                seen[row_id] = event
    return True