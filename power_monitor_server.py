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
    return f"{h}год {m}хв"

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
        
        if today_str not in schedule_data or not schedule_data[today_str].get('slots'):
            return (None, None, "Графік відсутній")
            
        slots = schedule_data[today_str]['slots']
        current_slot_idx = (now.hour * 2) + (1 if now.minute >= 30 else 0)
        
        # True = Light, False = Outage
        is_light_now = slots[current_slot_idx]
        
        # Find end of current block
        end_idx = 48
        for i in range(current_slot_idx + 1, 48):
            if slots[i] != is_light_now:
                end_idx = i
                break
        
        t_end = f"{end_idx//2:02}:{ '30' if end_idx%2 and end_idx<48 else '00'}"
        if end_idx == 48: t_end = "23:59"
        
        # Find next block range
        next_start_idx = end_idx
        if next_start_idx < 48:
            next_end_idx = 48
            for i in range(next_start_idx + 1, 48):
                if slots[i] == is_light_now:
                    next_end_idx = i
                    break
            
            ns_t = f"{next_start_idx//2:02}:{ '30' if next_start_idx%2 else '00'}"
            ne_t = f"{next_end_idx//2:02}:{ '30' if next_end_idx%2 and next_end_idx<48 else '00'}"
            if next_end_idx == 48: ne_t = "23:59"
            next_range = f"{ns_t} - {ne_t}"
        else:
            next_range = "до кінця доби"
            
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
                last_seen = state["last_seen"]
                
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
                        duration = "?"
                    
                    sched_light_now, current_end, next_range = get_schedule_context()
                    if sched_light_now is False: # Should be dark
                        sched_msg = f"По графіку світла не мало бути до {current_end}, але нам сьогодні щастить більше"
                    else:
                        sched_msg = f"Наступне планове: {next_range}"

                    time_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M")
                    
                    msg = (f"🟢 <b>{time_str} ТАК💡Світло є!</b>\n"
                           f"🕓 Його не було {duration}\n"
                           f"🗓 {sched_msg}")
                    
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
                
                # Assume outage happened 1 min after last ping (since pings are every 1 min)
                down_time_ts = last_seen + 60
                state["went_down_at"] = down_time_ts
                log_event("down", down_time_ts)
                
                # Calculate how long it was UP (using accurate down time)
                if state["came_up_at"] > 0:
                    duration = format_duration(down_time_ts - state["came_up_at"])
                else:
                    duration = "?"
                
                sched_light_now, current_end, next_range = get_schedule_context()
                if sched_light_now is True: # Should be light
                    sched_msg = f"По графіку світло має бути до {current_end}, можливо діють екстренні відключення"
                else:
                    sched_msg = f"Очікуємо за графіком: {next_range}"

                time_str = datetime.datetime.fromtimestamp(down_time_ts, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M")
                
                msg = (f"🔴 <b>{time_str} Зникло ❌  хай йому грець!</b>\n"
                       f"🕓 Воно було {duration}\n"
                       f"🗓 {sched_msg}")
                
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
