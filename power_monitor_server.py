import http.server
import socketserver
import threading
import time
import json
import os
import secrets
import datetime
import requests
from urllib.parse import urlparse, parse_qs

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
PORT = 8889
# SECRET_KEY handled in state
STATE_FILE = "power_monitor_state.json"
SCHEDULE_FILE = "last_schedules.json"
EVENT_LOG_FILE = "event_log.json"
TZ_OFFSET = 2  # UTC+2 (EET), adjust for DST manually or use pytz if available (avoiding extra deps for now)

# --- State Management ---
state = {
    "status": "unknown",  # up, down, unknown
    "last_seen": 0,
    "went_down_at": 0,
    "came_up_at": 0,
    "secret_key": None
}

state_lock = threading.RLock()

def log_event(event_type, timestamp):
    """
    Logs an event (up/down) to a JSON file for historical analysis.
    """
    try:
        entry = {
            "timestamp": timestamp,
            "event": event_type,
            "date_str": datetime.datetime.fromtimestamp(timestamp, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Read existing logs or create new list
        logs = []
        if os.path.exists(EVENT_LOG_FILE):
            try:
                with open(EVENT_LOG_FILE, 'r') as f:
                    content = f.read().strip()
                    if content:
                        logs = json.loads(content)
                        if not isinstance(logs, list):
                            logs = []
            except (json.JSONDecodeError, FileNotFoundError):
                logs = []
            
        logs.append(entry)
        
        # Keep roughly last ~30 days (assuming ~20 events/day max = 600 events)
        if len(logs) > 1000: 
            logs = logs[-1000:]
            
        with open(EVENT_LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=2)
            
    except Exception as e:
        print(f"Failed to log event: {e}")

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                saved_state = json.load(f)
                state.update(saved_state)
        except Exception as e:
            print(f"Error loading state: {e}")
    
    if not state.get("secret_key"):
        state["secret_key"] = secrets.token_urlsafe(16)
        save_state()

def save_state():
    with state_lock:
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"Error saving state: {e}")

def get_current_time():
    # Returns local time timestamp
    return time.time()

def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h} год {m} хв"

def get_schedule_context():
    try:
        with open(SCHEDULE_FILE, 'r') as f:
            data = json.load(f)
        
        source = data.get('yasno') or data.get('github')
        if not source: return (None, None, "Невідомо")
        
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        if today_str not in schedule_data or not schedule_data[today_str].get('slots'):
            return (None, None, "Графік відсутній")
            
        # Combine today and tomorrow slots for a 48h view (96 slots)
        slots = list(schedule_data[today_str]['slots'])
        if tomorrow_str in schedule_data and schedule_data[tomorrow_str].get('slots'):
            slots.extend(schedule_data[tomorrow_str]['slots'])
        else:
            # If no tomorrow data, pad with the last state of today
            slots.extend([slots[-1]] * 48)
            
        current_slot_idx = (now.hour * 2) + (1 if now.minute >= 30 else 0)
        
        # True = Light, False = Outage
        is_light_now = slots[current_slot_idx]
        
        # Find end of current block (max 96 slots)
        end_idx = len(slots)
        for i in range(current_slot_idx + 1, len(slots)):
            if slots[i] != is_light_now:
                end_idx = i
                break
        
        # Format end time
        def format_idx_to_time(idx):
            if idx >= 96: return "час очікується"
            day_offset = idx // 48
            rem_idx = idx % 48
            h = rem_idx // 2
            m = 30 if rem_idx % 2 else 0
            
            if day_offset == 0:
                return f"{h:02d}:{m:02d}"
            elif day_offset == 1:
                return f"завтра о {h:02d}:{m:02d}"
            else:
                return "післязавтра"

        t_end = format_idx_to_time(end_idx)
        
        # Find next block range
        next_start_idx = end_idx
        if next_start_idx < len(slots):
            # If we need to show the range of the NEXT block
            # But the next block is in tomorrow and tomorrow is empty/padded
            if next_start_idx >= 48 and (tomorrow_str not in schedule_data or not schedule_data[tomorrow_str].get('slots')):
                next_range = "час очікується"
            else:
                next_end_idx = len(slots)
                for i in range(next_start_idx + 1, len(slots)):
                    if slots[i] == is_light_now:
                        next_end_idx = i
                        break
                
                ns_t = format_idx_to_time(next_start_idx)
                ne_t = format_idx_to_time(next_end_idx)
                next_range = f"{ns_t} - {ne_t}"
        else:
            next_range = "час очікується"
            
        return (is_light_now, t_end, next_range)
            
    except Exception as e:
        print(f"Schedule error: {e}")
        return (None, None, "Помилка")

def send_telegram(message):
    # Mask token for logging
    token_masked = TOKEN[:5] + "..." + TOKEN[-5:] if TOKEN else "None"
    print(f"DEBUG: Sending telegram message to {CHAT_ID} via bot {token_masked}")
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        print(f"DEBUG: Telegram Response: {r.status_code} {r.text}")
        if r.status_code != 200:
            print(f"Telegram API Error: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def get_deviation_info(event_time, is_up):
    # event_time: timestamp (float)
    # is_up: True if light appeared, False if disappeared
    
    try:
        if not os.path.exists(SCHEDULE_FILE):
            return ""
            
        with open(SCHEDULE_FILE, 'r') as f:
            data = json.load(f)
        
        # Priority: Yasno -> Github
        source = data.get('yasno') or data.get('github')
        if not source: return ""
        
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        # Localize event time
        dt = datetime.datetime.fromtimestamp(event_time, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        date_str = dt.strftime("%Y-%m-%d")
        
        if date_str not in schedule_data or not schedule_data[date_str].get('slots'):
            return ""
            
        slots = schedule_data[date_str]['slots']
        
        # Current slot index (0-47)
        # 12:35 -> 12*2 + 1 = 25 (12:30-13:00)
        current_idx = (dt.hour * 2) + (1 if dt.minute >= 30 else 0)
        
        # Find nearest transition
        # We look for index 'i' where slots[i] != slots[i-1]
        # Transition happens exactly at start of slot 'i' (i*30 min from start of day)
        
        best_diff = 9999
        transition_type = None # 'up' or 'down'
        
        # Check all possible transition points in the day (0..48)
        # 0 is start of day (00:00), 48 is end of day (24:00)
        for i in range(49):
            # Determine state before and after point i
            # State BEFORE point i is slots[i-1] (if i>0)
            # State AFTER point i is slots[i] (if i<48)
            
            state_before = slots[i-1] if i > 0 else slots[0] # assume start matches 00:00 state
            state_after = slots[i] if i < 48 else slots[47] # assume end matches 23:30 state
            
            if i == 0: state_before = not state_after # Force check at 00:00? No, rely on prev day? Simpler: ignore 00:00 unless explicit change
            
            if state_before != state_after:
                # Found transition at point i
                # Time of transition: i * 30 minutes from 00:00
                trans_h = i // 2
                trans_m = 30 if i % 2 else 0
                
                # Create naive datetime for transition
                trans_dt = dt.replace(hour=trans_h, minute=trans_m, second=0, microsecond=0)
                
                # Diff in minutes
                diff = (dt - trans_dt).total_seconds() / 60
                
                if abs(diff) < abs(best_diff):
                    best_diff = int(diff)
                    # Determine type
                    if not state_before and state_after:
                        transition_type = 'up'
                    else:
                        transition_type = 'down'

        # Filter out if too far (e.g. > 90 min away)
        if abs(best_diff) > 90:
            return ""

        # Logic check: Did the event match the schedule change?
        # e.g. Light went DOWN, and nearest schedule change was DOWN -> Good
        expected_type = 'up' if is_up else 'down'
        
        if transition_type != expected_type:
            return "" # Probably random failure, not schedule related

        # Format output
        sign = "+" if best_diff > 0 else "−"
        # + means Event was LATER than Schedule (Delay)
        # - means Event was EARLIER than Schedule (Early)
        
        action = "увімкнення" if is_up else "вимкнення"
        
        if best_diff > 0:
            label = f"запізнення {action}"
        else:
            label = f"раніше {action}"
            
        return f"• Точність: {sign}{abs(best_diff)} хв ({label})"

    except Exception as e:
        print(f"Error in deviation calc: {e}")
        return ""

# --- Heartbeat Handler ---
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == f"/api/push/{state['secret_key']}":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            with state_lock:
                current_time = get_current_time()
                previous_status = state["status"]
                
                # Update heartbeat
                state["last_seen"] = current_time
                
                # Logic: If we were DOWN, and now we get a request -> We are UP
                if previous_status == "down" or previous_status == "unknown":
                    state["status"] = "up"
                    state["came_up_at"] = current_time
                    log_event("up", current_time)
                    
                    # Calculate outage duration
                    if state["went_down_at"] > 0:
                        duration = format_duration(current_time - state["went_down_at"])
                    else:
                        duration = "невідомо"
                    
                    sched_light_now, current_end, next_range = get_schedule_context()
                    
                    time_str = datetime.datetime.fromtimestamp(current_time, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M")
                    dev_msg = get_deviation_info(current_time, True)
                    
                    # Header
                    msg = f"🟢 <b>{time_str} Світло з'явилося</b>\n\n"
                    
                    # Stats Block
                    msg += "📊 <b>Статистика відключення:</b>\n"
                    msg += f"• Світла не було: {duration}\n"
                    if dev_msg:
                        msg += f"{dev_msg}\n"
                    
                    # Schedule Block
                    msg += "\n🗓 <b>Аналіз:</b>\n"
                    if sched_light_now is False: # Should be dark
                        msg += f"• За графіком світло мало з'явитися о: <b>{current_end}</b>\n"
                        # Extract the END time of the next light slot (when the next outage starts)
                        next_off_time = next_range.split(' - ')[1] if ' - ' in next_range else "24:00"
                        msg += f"• Наступне вимкнення: <b>{next_off_time}</b>"
                    else:
                        msg += f"• Зараз за графіком — <b>час зі світлом</b>\n"
                        msg += f"• Наступне вимкнення: <b>{current_end}</b>"
                    
                    threading.Thread(target=send_telegram, args=(msg,)).start()
                
                save_state()
            
            self.wfile.write(b'{"status": "ok", "msg": "heartbeat_received"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return # Silence logs

# --- Monitor Loop ---
def monitor_loop():
    print("Monitor loop started...")
    while True:
        time.sleep(60) # Check every minute
        
        with state_lock:
            current_time = get_current_time()
            last_seen = state["last_seen"]
            status = state["status"]
            
            # Timeout threshold: 3 minutes (180 seconds)
            if status == "up" and (current_time - last_seen) > 180:
                # Timeout detected!
                state["status"] = "down"
                
                # Assume outage happened 1 min after last ping
                down_time_ts = last_seen + 60
                state["went_down_at"] = down_time_ts
                log_event("down", down_time_ts)
                
                # Calculate how long it was UP
                if state["came_up_at"] > 0:
                    duration = format_duration(down_time_ts - state["came_up_at"])
                else:
                    duration = "невідомо"
                
                sched_light_now, current_end, next_range = get_schedule_context()
                
                time_str = datetime.datetime.fromtimestamp(down_time_ts, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M")
                dev_msg = get_deviation_info(down_time_ts, False)
                
                # Header
                msg = f"🔴 <b>{time_str} Світло зникло!</b>\n\n"
                
                # Stats Block
                msg += "📊 <b>Статистика сесії:</b>\n"
                msg += f"• Світло було: {duration}\n"
                if dev_msg:
                    msg += f"{dev_msg}\n"
                
                # Schedule Block
                msg += "\n🗓 <b>Прогноз:</b>\n"
                if sched_light_now is True: # Should be light
                    msg += f"• Очікуємо за графіком о: <b>{next_range.split(' - ')[0] if ' - ' in next_range else next_range}</b>\n"
                    msg += f"• Аналіз: <b>За графіком світло мало бути до {current_end}</b>"
                else:
                    msg += f"• Очікуємо за графіком о: <b>{current_end}</b>"

                threading.Thread(target=send_telegram, args=(msg,)).start()
                save_state()

# --- Main Execution ---
if __name__ == "__main__":
    print(f"Starting Power Monitor Server on port {PORT}...", flush=True)
    if not TOKEN or not CHAT_ID:
        print("ERROR: Token or Chat ID missing!", flush=True)
    else:
        print(f"Config loaded. ChatID: {CHAT_ID}", flush=True)
        
    load_state()
    print(f"Push URL: http://<YOUR_IP>:{PORT}/api/push/{state['secret_key']}")
    
    # Write the URL to a file so the user can see it easily
    with open("power_push_url.txt", "w") as f:
        f.write(f"http://46.224.186.236:{PORT}/api/push/{state['secret_key']}")

    # Start Monitor Thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start HTTP Server
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("", PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server...")
        server.server_close()
