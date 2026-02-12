import json
import os

HISTORY_FILE = "/root/geminicli/light-monitor-kyiv/schedule_history.json"

# Load existing history
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
else:
    history = {}

# Monday 2026-02-09
# 00:00-02:30 ON (5 slots)
# 02:30-10:30 OFF (16 slots)
# 10:30-13:00 ON (5 slots)
# 13:00-20:00 OFF (14 slots)
# 20:00-23:30 ON (7 slots)
# 23:30-24:00 OFF (1 slot)
mon_slots = [True]*5 + [False]*16 + [True]*5 + [False]*14 + [True]*7 + [False]*1

# Tuesday 2026-02-10
# 00:00-06:30 OFF (13 slots)
# 06:30-10:00 ON (7 slots)
# 10:00-17:00 OFF (14 slots)
# 17:00-19:30 ON (5 slots)
# 19:30-24:00 OFF (9 slots)
tue_slots = [False]*13 + [True]*7 + [False]*14 + [True]*5 + [False]*9

# Wednesday 2026-02-11
# 00:00-03:00 OFF (6 slots)
# 03:00-07:00 ON (8 slots)
# 07:00-14:00 OFF (14 slots)
# 14:00-16:30 ON (5 slots)
# 16:30-24:00 OFF (15 slots)
wed_slots = [False]*6 + [True]*8 + [False]*14 + [True]*5 + [False]*15

# Update history
history["2026-02-09"] = mon_slots
history["2026-02-10"] = tue_slots
history["2026-02-11"] = wed_slots

# Save
with open(HISTORY_FILE, "w") as f:
    json.dump(history, f, indent=2)

print("Schedule history updated for Mon-Wed.")
