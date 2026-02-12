import json
import os
from datetime import datetime, timezone, timedelta

EVENT_LOG_FILE = "/root/geminicli/light-monitor-kyiv/event_log.json"
TZ_OFFSET = 2
KYIV_TZ = timezone(timedelta(hours=TZ_OFFSET))

def get_ts(y, m, d, H, M):
    dt = datetime(y, m, d, H, M, tzinfo=KYIV_TZ)
    return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S")

new_events_data = [
    # 09.02 (Mon)
    (2026, 2, 9, 4, 17, 'down'),
    (2026, 2, 9, 10, 12, 'up'),
    (2026, 2, 9, 13, 9, 'down'),
    (2026, 2, 9, 14, 24, 'up'),
    (2026, 2, 9, 14, 57, 'down'),
    (2026, 2, 9, 15, 4, 'up'),
    (2026, 2, 9, 15, 6, 'down'),
    (2026, 2, 9, 19, 40, 'up'),
    (2026, 2, 9, 23, 33, 'down'),
    # 10.02 (Tue)
    (2026, 2, 10, 6, 11, 'up'),
    (2026, 2, 10, 11, 48, 'down'),
    (2026, 2, 10, 16, 1, 'up'),
    # 11.02 (Wed) - Already in log, but adding to be safe (dedup logic will handle)
    (2026, 2, 11, 8, 31, 'down'),
    (2026, 2, 11, 9, 49, 'up'),
    (2026, 2, 11, 9, 51, 'down')
]

# Load existing
existing_events = []
if os.path.exists(EVENT_LOG_FILE):
    try:
        with open(EVENT_LOG_FILE, 'r') as f:
            existing_events = json.load(f)
    except:
        pass

# Convert new data to dicts
new_events = []
for y, m, d, H, M, evt in new_events_data:
    ts, d_str = get_ts(y, m, d, H, M)
    new_events.append({
        "timestamp": ts,
        "event": evt,
        "date_str": d_str
    })

# Merge and Dedup
# We use a set of (timestamp_int, event) to detect dupes. 
# Timestamp int to ignore microsecond diffs.
seen = set()
merged = []

# Add existing first
for e in existing_events:
    key = (int(e['timestamp']), e['event'])
    if key not in seen:
        seen.add(key)
        merged.append(e)

# Add new
for e in new_events:
    key = (int(e['timestamp']), e['event'])
    if key not in seen:
        seen.add(key)
        merged.append(e)
        print(f"Added: {e['date_str']} {e['event']}")
    else:
        print(f"Skipped (duplicate): {e['date_str']} {e['event']}")

# Sort by timestamp
merged.sort(key=lambda x: x['timestamp'])

# Save
with open(EVENT_LOG_FILE, 'w') as f:
    json.dump(merged, f, indent=2)

print(f"Total events: {len(merged)}")
