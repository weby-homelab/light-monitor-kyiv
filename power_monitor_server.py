import http.server
import socketserver
import threading
import time
import json
import os
import secrets
import datetime
from zoneinfo import ZoneInfo
import requests
import subprocess
from urllib.parse import urlparse, parse_qs

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
PORT = 8889
# SECRET_KEY handled in state
STATE_FILE = "power_monitor_state.json"
SCHEDULE_FILE = "last_schedules.json"
EVENT_LOG_FILE = "event_log.json"
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# --- State Management ---
state = {
    "status": "unknown",  # up, down, unknown
    "last_seen": 0,
    "went_down_at": 0,
    "came_up_at": 0,
    "secret_key": None
}

state_lock = threading.RLock()

def trigger_daily_report_update():
    """
    Triggers the generation and update of the daily report chart.
    Runs asynchronously to not block the main thread.
    """
    def run_script():
        try:
            print("Triggering daily report update...")
            # Assuming we are running in the project root or venv is available
            # We use absolute path to be safe based on service file
            python_exec = "/root/geminicli/light-monitor-kyiv/venv/bin/python"
            script_path = "/root/geminicli/light-monitor-kyiv/generate_daily_report.py"
            
            # Run without --no-send so it updates Telegram
            subprocess.run([python_exec, script_path], check=True)
            
            # Also trigger weekly report update
            trigger_weekly_report_update()
            
        except Exception as e:
            print(f"Failed to trigger daily report: {e}")

    threading.Thread(target=run_script).start()

def trigger_weekly_report_update():
    """
    Triggers the generation of the weekly report chart for the web.
    """
    def run_script():
        try:
            print("Triggering weekly report update...")
            python_exec = "/root/geminicli/light-monitor-kyiv/venv/bin/python"
            script_path = "/root/geminicli/light-monitor-kyiv/generate_weekly_report.py"
            output_path = "/root/geminicli/light-monitor-kyiv/web/weekly.png"
            
            subprocess.run([python_exec, script_path, "--output", output_path], check=True)
        except Exception as e:
            print(f"Failed to trigger weekly report: {e}")

    threading.Thread(target=run_script).start()

def log_event(event_type, timestamp):
    """
    Logs an event (up/down) to a JSON file for historical analysis.
    """
    try:
        entry = {
            "timestamp": timestamp,
            "event": event_type,
            "date_str": datetime.datetime.fromtimestamp(timestamp, KYIV_TZ).strftime("%Y-%m-%d %H:%M:%S")
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
        if not source: return (None, None, "Невідомо", None)
        
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        now = datetime.datetime.now(KYIV_TZ)
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        if today_str not in schedule_data or not schedule_data[today_str].get('slots'):
            return (None, None, "Графік відсутній", None)
            
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
        next_duration = None
        
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
                
                # Calculate duration
                dur_h = (next_end_idx - next_start_idx) * 0.5
                next_duration = f"{dur_h:g}".replace('.', ',')
        else:
            next_range = "час очікується"
            
        return (is_light_now, t_end, next_range, next_duration)
            
    except Exception as e:
        print(f"Schedule error: {e}")
        return (None, None, "Помилка", None)

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
        dt = datetime.datetime.fromtimestamp(event_time, KYIV_TZ)
        date_str = dt.strftime("%Y-%m-%d")
        
        if date_str not in schedule_data or not schedule_data[date_str].get('slots'):
            return ""
            
        slots = schedule_data[date_str]['slots']
        
        # Find nearest transition
        best_diff = 9999
        transition_type = None # 'up' or 'down'
        
        for i in range(49):
            state_before = slots[i-1] if i > 0 else slots[0] 
            state_after = slots[i] if i < 48 else slots[47] 
            
            if i == 0: state_before = not state_after 
            
            if state_before != state_after:
                trans_h = i // 2
                trans_m = 30 if i % 2 else 0
                
                trans_dt = dt.replace(hour=trans_h, minute=trans_m, second=0, microsecond=0)
                diff = (dt - trans_dt).total_seconds() / 60
                
                if abs(diff) < abs(best_diff):
                    best_diff = int(diff)
                    if not state_before and state_after:
                        transition_type = 'up'
                    else:
                        transition_type = 'down'

        if abs(best_diff) > 90:
            return ""

        expected_type = 'up' if is_up else 'down'
        if transition_type != expected_type:
            return "" 

        if best_diff == 0:
            return "• Точність: 0 хв (точно за графіком)"

        sign = "+" if best_diff > 0 else "−"
        action = "увімкнення" if is_up else "вимкнення"
        label = f"запізнення {action}" if best_diff > 0 else f"раніше {action}"
            
        return f"• Точність: {sign}{abs(best_diff)} хв ({label})"

    except Exception as e:
        print(f"Error in deviation calc: {e}")
        return ""

def get_nearest_schedule_switch(event_time, target_is_up):
    """
    Finds the nearest scheduled switch time for the given event.
    target_is_up: True if we are looking for ON switch, False for OFF.
    Returns: Formatted time string "HH:MM" or None.
    """
    try:
        if not os.path.exists(SCHEDULE_FILE): return None
        with open(SCHEDULE_FILE, 'r') as f: data = json.load(f)
        
        source = data.get('yasno') or data.get('github')
        if not source: return None
        
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        dt = datetime.datetime.fromtimestamp(event_time, KYIV_TZ)
        date_str = dt.strftime("%Y-%m-%d")
        
        if date_str not in schedule_data or not schedule_data[date_str].get('slots'):
            return None
            
        slots = schedule_data[date_str]['slots']
        
        best_diff = 9999
        best_time_str = None
        
        for i in range(49):
            state_before = slots[i-1] if i > 0 else slots[0]
            state_after = slots[i] if i < 48 else slots[47]
            if i == 0: state_before = not state_after
            
            if state_before != state_after:
                # Check if this transition matches our target
                # OFF->ON (Up) is state_after=True
                # ON->OFF (Down) is state_after=False
                is_up_switch = state_after
                
                if is_up_switch == target_is_up:
                    trans_h = i // 2
                    trans_m = 30 if i % 2 else 0
                    trans_dt = dt.replace(hour=trans_h, minute=trans_m, second=0, microsecond=0)
                    
                    diff = abs((dt - trans_dt).total_seconds())
                    if diff < best_diff:
                        best_diff = diff
                        best_time_str = f"{trans_h:02d}:{trans_m:02d}"
                        
        if best_diff > 5400: # If closest is more than 1.5 hours away, ignore
            return None
            
        return best_time_str
    except:
        return None

# --- Heartbeat Handler ---
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        
        # 1. Status Page (Root)
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            
            with state_lock:
                status_color = "#4CAF50" if state["status"] == "up" else "#EF9A9A"
                status_text = "СВІТЛО Є" if state["status"] == "up" else "СВІТЛА НЕМАЄ"
                last_event_ts = state["came_up_at"] if state["status"] == "up" else state["went_down_at"]
                
                duration = "?"
                if last_event_ts > 0:
                    duration = format_duration(time.time() - last_event_ts)
                
                last_ping = "ніколи"
                if state["last_seen"] > 0:
                    last_ping = datetime.datetime.fromtimestamp(state["last_seen"], KYIV_TZ).strftime("%H:%M:%S")

            # Get Group Name
            group_name = "Невідома група"
            try:
                if os.path.exists(SCHEDULE_FILE):
                    with open(SCHEDULE_FILE, 'r') as f:
                        sched_data = json.load(f)
                        src = sched_data.get('yasno') or sched_data.get('github')
                        if src:
                            group_key = list(src.keys())[0]
                            group_name = group_key.replace("GPV", "Група ")
            except:
                pass

            # Get Analytics & History
            analytics_html = ""
            history_html = ""
            weekly_chart_html = ""
            
            page_updated = datetime.datetime.now(KYIV_TZ).strftime("%d.%m.%Y %H:%M:%S")
            
            # --- Weekly Chart ---
            weekly_chart_path = "web/weekly.png"
            if os.path.exists(weekly_chart_path):
                 weekly_chart_html = """
                 <div class="card">
                     <div class="title">Тижневий графік</div>
                     <img src="/weekly.png" class="chart" alt="Тижневий графік">
                 </div>
                 """
            
            # --- Event History ---
            try:
                if os.path.exists(EVENT_LOG_FILE):
                    with open(EVENT_LOG_FILE, 'r') as f:
                        logs = json.load(f)
                        
                        # Calculate durations
                        for i in range(len(logs)):
                            if i > 0:
                                diff = logs[i]['timestamp'] - logs[i-1]['timestamp']
                                logs[i]['duration_prev'] = diff
                            else:
                                logs[i]['duration_prev'] = None

                        # Last 10 events, reversed
                        last_logs = logs[-10:][::-1]
                        
                        rows = ""
                        for log in last_logs:
                            ts = log.get('timestamp', 0)
                            evt = log.get('event', 'unknown')
                            dur_sec = log.get('duration_prev')
                            
                            dt_str = datetime.datetime.fromtimestamp(ts, KYIV_TZ).strftime("%d.%m %H:%M")
                            
                            color = "#4CAF50" if evt == "up" else "#EF9A9A"
                            icon = "🟢" if evt == "up" else "🔴"
                            
                            if evt == "up":
                                base_text = "Світло з'явилося"
                                if dur_sec:
                                    dur_str = format_duration(dur_sec)
                                    text = f"{base_text}<br><span style=\'font-weight:normal; font-size: 0.9em; color: #AAA; text-align: right; display: block;\'>(не було {dur_str})</span>"
                                else:
                                    text = base_text
                            else:
                                base_text = "Світло зникло"
                                if dur_sec:
                                    dur_str = format_duration(dur_sec)
                                    text = f"{base_text}<br><span style=\'font-weight:normal; font-size: 0.9em; color: #AAA; text-align: right; display: block;\'>(було {dur_str})</span>"
                                else:
                                    text = base_text
                            
                            rows += f"""
                            <tr>
                                <td style="white-space: nowrap; text-align: left;">{dt_str}</td>
                                <td style="color: {color}; font-weight: bold; text-align: left;">{icon} {text}</td>
                            </tr>
                            """
                        
                        history_html = f"""
                        <div class="card">
                            <div class="title">Останні події</div>
                            <table>
                                {rows}
                            </table>
                        </div>
                        """
            except Exception as e:
                print(f"Error reading history: {e}")

            try:
                stats_file = "web/stats.json"
                if os.path.exists(stats_file):
                    with open(stats_file, 'r') as f:
                        s = json.load(f)
                        sign = "+" if s['diff'] > 0 else ""
                        diff_str = f"{sign}{s['diff']:.1f}год"
                        
                        analytics_html = f"""
                        <div class="card">
                            <div class="title">План vs Факт (Сьогодні)</div>
                            <div style="font-size: 16px; margin-bottom: 5px; color: #CCC;">• За планом: <b style="color: #fff;">{s['plan_up']}</b></div>
                            <div style="font-size: 16px; margin-bottom: 5px; color: #CCC;">• Реально: <b style="color: #fff;">{s['fact_up']}</b></div>
                            <div style="font-size: 16px; color: #CCC;">• Відхилення: <b style="color: #fff;">{diff_str}</b> ({s['pct']}% від плану)</div>
                        </div>
                        """
            except Exception as e:
                print(f"Error reading stats: {e}")

            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Монітор живлення</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <meta name="robots" content="noindex, nofollow">
                <meta http-equiv="refresh" content="60">
                
                <!-- PWA Settings -->
                <link rel="manifest" href="/manifest.json">
                <meta name="theme-color" content="#1E122A">
                <link rel="icon" type="image/svg+xml" href="/icon.svg">
                <link rel="apple-touch-icon" href="/icon.svg">
                <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">

                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #1E122A; color: white; text-align: center; padding: 20px; margin: 0; }}
                    .container {{ max-width: 800px; margin: 0 auto; }}
                    
                    h1 {{
                        font-weight: 500;
                        letter-spacing: 0.5px;
                        color: #E0E0E0;
                        margin-bottom: 30px;
                    }}

                    .card {{ 
                        background: #1E122A; 
                        border-radius: 12px; 
                        padding: 20px; 
                        margin-bottom: 20px; 
                        box-shadow: 0 5px 15px rgba(0,0,0,0.3);
                        text-align: left;
                        border: 1px solid #1E122A; /* Reverted border color */
                    }}
                    
                    .title {{ color: #aaa; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; font-weight: 600; }}
                    .value {{ font-size: 28px; font-weight: bold; }}
                    .status-text {{ font-size: 16px; margin-top: 5px; color: #ccc; }}
                    
                    .status-card .value {{ font-size: 36px; text-align: center; }}
                    .status-card .status-text {{ text-align: center; }}
                    
                    .chart {{ width: 100%; border-radius: 8px; margin-top: 10px; }}
                    
                    .group {{ text-align: center; margin-top: -10px; margin-bottom: 20px; font-size: 16px; color: #BBB; font-weight: bold; }}

                    .footer {{ margin-top: 40px; font-size: 12px; color: #666; text-align: center;}}
                    .footer a {{ color: #888; text-decoration: none; }}
                    .footer a:hover {{ color: #fff; text-decoration: underline; }}
                    
                    /* Table Styles */
                    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                    td {{ padding: 10px 8px; border-bottom: 1px solid #3a2d4d; }}
                    tr:last-child td {{ border-bottom: none; }}

                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Монітор живлення</h1>
                    
                    <div class="card status-card">
                        <div class="title" style="text-align: center;">Поточний статус</div>
                        <div class="value" style="color: {status_color};">{status_text}</div>
                        <div class="status-text">
                            Тривалість: <b>{duration}</b><br>Останній сигнал: <b>{last_ping}</b>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="title">Графік за сьогодні ({group_name})</div>
                        <img src="/chart.png?v={int(time.time())}" class="chart" alt="Графік за сьогодні">
                    </div>
                    
                    {analytics_html}
                    
                    {weekly_chart_html}
                    
                    {history_html}
                    
                    <div class="footer">
                        Оновлено: {page_updated}<br>
                        <a href="https://github.com/weby-homelab/light-monitor-kyiv" target="_blank">GitHub: light-monitor-kyiv</a>
                    </div>
                </div>
                <script>
                    if ('serviceWorker' in navigator) {{
                        window.addEventListener('load', () => {{
                            navigator.serviceWorker.register('/service-worker.js')
                                .then(reg => console.log('SW registered!', reg))
                                .catch(err => console.log('SW failed!', err));
                        }});
                    }}
                </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
            return

        # 1.5 Chart Image (Daily)
        if parsed.path.startswith("/chart.png"):
            chart_path = "web/chart.png"
            if os.path.exists(chart_path):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                with open(chart_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
            return

        # 1.6 Chart Image (Weekly)
        if parsed.path == "/weekly.png":
            chart_path = "web/weekly.png"
            if os.path.exists(chart_path):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                with open(chart_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
            return

        # --- PWA Static Files ---
        if parsed.path in ["/manifest.json", "/icon.svg", "/service-worker.js"]:
            file_map = {
                "/manifest.json": ("application/json", "web/manifest.json"),
                "/icon.svg": ("image/svg+xml", "web/icon.svg"),
                "/service-worker.js": ("text/javascript", "web/service-worker.js")
            }
            content_type, file_path = file_map[parsed.path]
            
            if os.path.exists(file_path):
                self.send_response(200)
                self.send_header("Content-type", content_type)
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
            return

        # 1.6 Robots.txt
        if parsed.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nAllow: /")
            return

        # 2. Push API
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
                    
                    sched_light_now, current_end, next_range, next_duration = get_schedule_context()
                    
                    time_str = datetime.datetime.fromtimestamp(current_time, KYIV_TZ).strftime("%H:%M")
                    dev_msg = get_deviation_info(current_time, True)
                    
                    # Header
                    msg = f"🟢 <b>{time_str} Світло з'явилося</b>\n\n"
                    
                    # Stats Block
                    msg += "📊 <b>Статистика відключення:</b>\n"
                    msg += f"• Світла не було: <b>{duration}</b>\n"
                    if dev_msg:
                        msg += f"{dev_msg}\n"
                    
                    # Schedule Block
                    msg += "\n🗓 <b>Аналіз:</b>\n"
                    
                    sched_on_time = get_nearest_schedule_switch(current_time, True)
                    if sched_on_time:
                        msg += f"• За графіком світло мало з'явитися о: <b>{sched_on_time}</b>\n"
                    
                    if sched_light_now is False: # It appeared while it should be dark
                        next_off_time = next_range.split(' - ')[1] if ' - ' in next_range else "час очікується"
                        msg += f"• Наступне вимкнення: <b>{next_off_time}</b>"
                    else: # It appeared while it should be light
                        msg += f"• Наступне вимкнення: <b>{current_end}</b>"
                    
                    threading.Thread(target=send_telegram, args=(msg,)).start()
                    trigger_daily_report_update()
                
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
                
                sched_light_now, current_end, next_range, next_duration = get_schedule_context()
                
                time_str = datetime.datetime.fromtimestamp(down_time_ts, KYIV_TZ).strftime("%H:%M")
                dev_msg = get_deviation_info(current_time, False)
                
                # Header
                msg = f"🔴 <b>{time_str} Світло зникло!</b>\n\n"
                
                # Stats Block
                msg += "📊 <b>Статистика відключення:</b>\n"
                msg += f"• Світло було: <b>{duration}</b>\n"
                if dev_msg:
                    msg += f"{dev_msg}\n"
                
                # Schedule Block
                msg += "\n🗓 <b>Аналіз:</b>\n"
                
                scheduled_off_time = get_nearest_schedule_switch(down_time_ts, False)
                if scheduled_off_time:
                     msg += f"• За графіком світло мало зникнути о: <b>{scheduled_off_time}</b>\n"
                
                if sched_light_now is True: # Should be light (but went down)
                    expected_return = next_range.split(' - ')[1] if ' - ' in next_range else "час очікується"
                    msg += f"• Очікуємо увімкнення: <b>{expected_return}</b>"
                else:
                    msg += f"• Очікуємо увімкнення: <b>{current_end}</b>"

                threading.Thread(target=send_telegram, args=(msg,)).start()
                trigger_daily_report_update()
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
    server = socketserver.ThreadingTCPServer(("", PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server...")
        server.server_close()
