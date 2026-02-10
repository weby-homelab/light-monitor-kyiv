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
PORT = 8888
# SECRET_KEY handled in state
STATE_FILE = "power_monitor_state.json"
SCHEDULE_FILE = "last_schedules.json"
TZ_OFFSET = 2  # UTC+2 (EET), adjust for DST manually or use pytz if available (avoiding extra deps for now)

# --- State Management ---
state = {
    "status": "unknown",  # up, down, unknown
    "last_seen": 0,
    "went_down_at": 0,
    "came_up_at": 0,
    "secret_key": None
}

state_lock = threading.Lock()

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

def get_schedule_info(is_power_on):
    """
    Parses last_schedules.json to find the next change.
    Data format: "slots": [false, false, ..., true, true] (48 half-hour slots)
    true = OUTAGE (blackout), false = POWER ON (light)
    Wait, let's verify logic. Usually 'true' in blackout schedules means 'active outage'.
    Let's assume: true = OUTAGE, false = POWER.
    """
    try:
        with open(SCHEDULE_FILE, 'r') as f:
            data = json.load(f)
        
        # Priority: yasno -> github
        source = data.get('yasno') or data.get('github')
        if not source:
            return "Невідомо (джерело не знайдено)"
        
        # Get first group (e.g., "GPV36.1")
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        today_str = now.strftime("%Y-%m-%d")
        
        if today_str not in schedule_data or not schedule_data[today_str].get('slots'):
            return "Графік на сьогодні відсутній"
            
        slots = schedule_data[today_str]['slots'] # 48 items
        
        # Current slot index (0-47)
        current_slot_idx = (now.hour * 2) + (1 if now.minute >= 30 else 0)
        
        # Find next change
        found_change = False
        change_slot_idx = -1
        
        # Look ahead in today's slots
        # If power is ON (is_power_on=True), we look for next OUTAGE (true)
        # If power is OFF (is_power_on=False), we look for next POWER (false)
        target_state = True if is_power_on else False 
        
        for i in range(current_slot_idx, 48):
            if slots[i] == target_state:
                change_slot_idx = i
                found_change = True
                break
        
        if found_change:
            # Convert slot index back to time
            hour = change_slot_idx // 2
            minute = "30" if change_slot_idx % 2 == 1 else "00"
            return f"{hour:02}:{minute}"
        else:
            return "До кінця доби без змін"
            
    except Exception as e:
        return f"Помилка графіку: {str(e)}"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
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
                    
                    # Calculate outage duration
                    if state["went_down_at"] > 0:
                        duration = format_duration(current_time - state["went_down_at"])
                    else:
                        duration = "?"
                    
                    schedule_next = get_schedule_info(is_power_on=True)
                    time_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M")
                    
                    msg = (f"🟢 <b>{time_str} ТАК💡Світло є!</b>\n"
                           f"🕓 Його не було {duration}\n"
                           f"🗓 Наступне планове: {schedule_next}")
                    
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
                print("Timeout detected! Marking as DOWN.")
                state["status"] = "down"
                # We assume it went down roughly when we last saw it + some small buffer, 
                # but "last_seen" is the most accurate "last known alive" time.
                state["went_down_at"] = last_seen 
                
                # Calculate how long it was UP
                if state["came_up_at"] > 0:
                    duration = format_duration(last_seen - state["came_up_at"])
                else:
                    duration = "?"
                
                schedule_expect = get_schedule_info(is_power_on=False)
                time_str = datetime.datetime.fromtimestamp(last_seen + 180, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).strftime("%H:%M") # Use detection time for msg
                
                msg = (f"🔴 <b>{time_str} Зникло ❌  хай йому грець!</b>\n"
                       f"🕓 Воно було {duration}\n"
                       f"🗓 Очікуємо за графіком: {schedule_expect}")
                
                threading.Thread(target=send_telegram, args=(msg,)).start()
                save_state()

# --- Main Execution ---
if __name__ == "__main__":
    load_state()
    print(f"Starting Power Monitor Server on port {PORT}...")
    print(f"Push URL: http://<YOUR_IP>:{PORT}/api/push/{state['secret_key']}")
    
    # Write the URL to a file so the user can see it easily
    with open("power_push_url.txt", "w") as f:
        f.write(f"http://46.224.186.236:{PORT}/api/push/{state['secret_key']}")

    # Start Monitor Thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start HTTP Server
    server = socketserver.TCPServer(("", PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server...")
        server.server_close()
