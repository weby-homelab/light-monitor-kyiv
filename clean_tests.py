import json
import datetime

LOG_FILE = "/root/geminicli/light-monitor-kyiv/event_log.json"
STATE_FILE = "/root/geminicli/light-monitor-kyiv/power_monitor_state.json"

# 1. Clean Log
with open(LOG_FILE, 'r') as f:
    events = json.load(f)

# Keep events strictly BEFORE 12.02.2026 10:50:00 UTC+2
# Timestamp calculation:
# 2026-02-12 10:50:00 (Kyiv) -> 2026-02-12 08:50:00 (UTC)
# But timestamps in file are already timestamps.
# Let's verify the last real event.
# User said: "Сьогодні фактично світло не вимикали з 10:49".
# So the event at 10:49 "UP" is valid. Anything after is test.

# Let's find the timestamp of the 10:49 event to be precise or use a cutoff.
cutoff_ts = 0
valid_events = []

for e in events:
    # Check date string for safety
    if "2026-02-12 10:49" in e['date_str'] and e['event'] == 'up':
        valid_events.append(e)
        cutoff_ts = e['timestamp']
        # Stop adding subsequent events if they are on the same day and look like tests
        # Actually, simpler logic: filter by timestamp.
        continue
    
    # If timestamp > cutoff and cutoff is set (meaning we found the 10:49 point), discard.
    if cutoff_ts > 0 and e['timestamp'] > cutoff_ts:
        print(f"Removing test event: {e['date_str']} ({e['event']})")
        continue
        
    valid_events.append(e)

# Overwrite with simple filter if above logic is complex:
# Just keep everything <= 1770886172 (approx 10:49:32)
# Let's use the explicit list from loop.

with open(LOG_FILE, 'w') as f:
    json.dump(valid_events, f, indent=2)

print(f"Log cleaned. Kept {len(valid_events)} events.")

# 2. Reset State
# We need to tell the monitor that we are UP since 10:49.
with open(STATE_FILE, 'r') as f:
    state = json.load(f)

state['status'] = 'up'
state['came_up_at'] = cutoff_ts if cutoff_ts > 0 else state['came_up_at']
state['went_down_at'] = 0 # Clear any recent down time
# last_seen should be NOW to prevent immediate timeout
state['last_seen'] = datetime.datetime.now().timestamp()

with open(STATE_FILE, 'w') as f:
    json.dump(state, f)

print("State reset to UP.")
