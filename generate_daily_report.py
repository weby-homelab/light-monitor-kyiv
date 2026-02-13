import json
import os
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
import sys

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
EVENT_LOG_FILE = "event_log.json"
SCHEDULE_FILE = "last_schedules.json"
HISTORY_FILE = "schedule_history.json"
TZ_OFFSET = 2  # UTC+2 (EET)

def load_events():
    if not os.path.exists(EVENT_LOG_FILE):
        return []
    try:
        with open(EVENT_LOG_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def load_schedule_slots(target_date):
    """
    Returns the list of 48 boolean slots (True=Light, False=Outage) for the target date.
    Checks last_schedules.json first, then schedule_history.json.
    """
    date_str = target_date.strftime("%Y-%m-%d")
    
    # 1. Try last_schedules.json (current/future)
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, 'r') as f:
                data = json.load(f)
            
            # Priority: Yasno -> Github
            source = data.get('yasno') or data.get('github')
            if source:
                group_key = list(source.keys())[0]
                schedule_data = source[group_key]
                
                if date_str in schedule_data and schedule_data[date_str].get('slots'):
                     return schedule_data[date_str]['slots']
        except Exception as e:
            print(f"Error loading schedule: {e}")

    # 2. Try schedule_history.json (past)
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
                if date_str in history:
                    return history[date_str]
        except Exception as e:
            print(f"Error loading history: {e}")
            
    return None

def get_intervals_for_date(target_date, events):
    """
    Returns a list of (start_time, end_time, state) for the target date.
    """
    
    # Target date range
    day_start = datetime.datetime.combine(target_date, datetime.time.min).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
    day_end = datetime.datetime.combine(target_date, datetime.time.max).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
    
    # If target is today, clip the calculation end to NOW for stats, 
    # but the chart X-axis will still cover the full day.
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
    if target_date == now.date():
        calc_end = now
    else:
        calc_end = day_end

    # Sort events by timestamp
    events.sort(key=lambda x: x['timestamp'])
    
    intervals = []
    
    # Determine initial state at 00:00
    current_state = "unknown"
    
    # Find the last event BEFORE the start of the day
    for event in events:
        if event['timestamp'] < day_start.timestamp():
            current_state = event['event']
        else:
            break
            
    current_time = day_start
    
    # Iterate through events strictly within the day
    for event in events:
        event_ts = event['timestamp']
        event_dt = datetime.datetime.fromtimestamp(event_ts, datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        
        if event_dt < day_start:
            continue
        if event_dt > calc_end:
            break
            
        # Add interval from current_time to this event
        if current_time < event_dt:
            intervals.append((current_time, event_dt, current_state))
            
        current_time = event_dt
        current_state = event['event']
        
    # Add final interval to end of calculation period
    if current_time < calc_end:
        intervals.append((current_time, calc_end, current_state))
        
    return intervals

def get_schedule_intervals(target_date, slots):
    """
    Converts 48 boolean slots into time intervals for the chart.
    Returns list of (start_time, duration_hours, is_light)
    """
    if not slots: return []
    
    intervals = []
    day_start = datetime.datetime.combine(target_date, datetime.time.min).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
    
    current_state = slots[0]
    start_idx = 0
    
    for i in range(1, 48):
        if slots[i] != current_state:
            # End of block
            end_idx = i
            
            # Create interval
            start_time = day_start + datetime.timedelta(minutes=start_idx*30)
            duration = (end_idx - start_idx) * 0.5 # hours
            intervals.append((start_time, duration, current_state))
            
            # Start new block
            current_state = slots[i]
            start_idx = i
            
    # Final block
    start_time = day_start + datetime.timedelta(minutes=start_idx*30)
    duration = (48 - start_idx) * 0.5
    intervals.append((start_time, duration, current_state))
    
    return intervals

def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}год {m}хв"

def generate_chart(target_date, intervals, schedule_intervals):
    # Dark Mode - Deep Purple Background
    with plt.style.context('dark_background'):
        # Reduced height again (was 2.4, now 2.0 -> thinner bars)
        fig, ax = plt.subplots(figsize=(10, 2.0), facecolor='#1E122A')
        ax.set_facecolor('#1E122A')
        
        # Define geometries - Glued together
        sched_y = 12.5
        sched_h = 2.5
        act_y = 15
        act_h = 2.5
        
        day_start = datetime.datetime.combine(target_date, datetime.time.min).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        day_end = datetime.datetime.combine(target_date, datetime.time.max).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        
        # --- Schedule Data (Bottom Bar) ---
        sched_color_map = {True: '#FFF59D', False: '#BDBDBD'} # Light Yellow, Gray
        
        if schedule_intervals:
            for start, duration_hours, is_light in schedule_intervals:
                color = sched_color_map.get(is_light, '#E0E0E0')
                start_num = mdates.date2num(start)
                duration_days = duration_hours / 24.0
                ax.broken_barh([(start_num, duration_days)], (sched_y, sched_h), facecolors=color, edgecolor='none')

        # --- Separator Line (Background Color) ---
        ax.axhline(y=15, color='#1E122A', linewidth=0.5, zorder=5)

        # --- Hour Markers on the Bars (Background Color) ---
        hour_points = [mdates.date2num(datetime.datetime.combine(target_date, datetime.time(h, 0))) for h in range(1, 24)]
        # Cover both Schedule (sched_y) and Actual (act_y + act_h) bars
        # sched_y = 12.5, sched_h = 2.5 -> top is 15
        # act_y = 15, act_h = 2.5 -> top is 17.5
        ax.vlines(hour_points, 12.5, 17.5, colors='#1E122A', linewidth=0.8, zorder=6)

        # --- Actual Data (Top Bar) ---
        color_map = {'up': '#4CAF50', 'down': '#EF9A9A', 'unknown': '#C8E6C9'}
        
        total_up = 0
        total_down = 0
        
        last_actual_end = day_start
        
        for start, end, state in intervals:
            duration_sec = (end - start).total_seconds()
            if state == 'up':
                total_up += duration_sec
            elif state == 'down':
                total_down += duration_sec
            elif state == 'unknown':
                total_up += duration_sec
                
            color = color_map.get(state, '#C8E6C9')
            
            start_num = mdates.date2num(start)
            end_num = mdates.date2num(end)
            duration_num = end_num - start_num
            
            ax.broken_barh([(start_num, duration_num)], (act_y, act_h), facecolors=color, edgecolor='none')
            
            if end > last_actual_end:
                last_actual_end = end

        # --- Future Bar ---
        if last_actual_end < day_end:
            start_num = mdates.date2num(last_actual_end)
            end_num = mdates.date2num(day_end)
            duration_num = end_num - start_num
            
            # Dark purple-grey for "future" in dark purple mode
            ax.broken_barh([(start_num, duration_num)], (act_y, act_h), facecolors='#3D2E4A', edgecolor='#5D4E6A', hatch='///', linewidth=0.5)

        # --- Formatting ---
        ax.set_ylim(11, 19) 
        ax.set_xlim(mdates.date2num(day_start), mdates.date2num(day_end))
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        ax.spines['bottom'].set_color('white')
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        
        ax.set_yticks([sched_y + sched_h/2, act_y + act_h/2])
        ax.set_yticklabels(['Графік', 'Факт'], color='white')
        
        ax.set_title(f"Статистика світла за {target_date.strftime('%d.%m.%Y')}", fontsize=12, color='white')
        
        import matplotlib.patches as mpatches
        green_patch = mpatches.Patch(color='#4CAF50', label=f'Світло є')
        red_patch = mpatches.Patch(color='#EF9A9A', label=f'Світла немає')
        yellow_patch = mpatches.Patch(color='#FFF59D', label='Графік: Є')
        gray_patch = mpatches.Patch(color='#BDBDBD', label='Графік: Немає')
        
        legend = plt.legend(handles=[green_patch, red_patch, yellow_patch, gray_patch], 
                   loc='upper center', bbox_to_anchor=(0.5, -0.25),
                   fancybox=False, frameon=False, shadow=False, ncol=4, fontsize='small')
        plt.setp(legend.get_texts(), color='white')

        plt.tight_layout()
        plt.subplots_adjust(bottom=0.35)
        
        filename = f"report_{target_date.strftime('%Y-%m-%d')}.png"
        plt.savefig(filename, dpi=100, facecolor=fig.get_facecolor())
        plt.close()
        
    return filename, total_up, total_down

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(photo_path, 'rb') as f:
        files = {'photo': f}
        data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML', 'disable_notification': True}
        try:
            r = requests.post(url, files=files, data=data, timeout=10)
            if r.status_code == 200:
                print("Report sent successfully.")
            else:
                print(f"Failed to send report: {r.text}")
        except Exception as e:
            print(f"Error sending report: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
        target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        target_date = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).date()
        
    print(f"Generating report for {target_date}...")
    
    events = load_events()
    slots = load_schedule_slots(target_date)
    
    intervals = get_intervals_for_date(target_date, events)
    sched_intervals = get_schedule_intervals(target_date, slots)
    
    if not intervals:
        day_start = datetime.datetime.combine(target_date, datetime.time.min).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        calc_end = now if target_date == now.date() else day_start + datetime.timedelta(hours=24)
        intervals = [(day_start, calc_end, "unknown")]

    filename, t_up, t_down = generate_chart(target_date, intervals, sched_intervals)
    
    caption = (f"📊 <b>Звіт за {target_date.strftime('%d.%m.%Y')}</b>\n\n"
               f"💡 Світло було: <b>{format_duration(t_up)}</b>\n"
               f"❌ Світла не було: <b>{format_duration(t_down)}</b>")

    if slots:
        plan_up_cnt = sum(1 for s in slots if s)
        plan_up_sec = plan_up_cnt * 1800  # 30 min * 60 sec
        
        diff_sec = t_up - plan_up_sec
        diff_hours = diff_sec / 3600
        sign = "+" if diff_hours > 0 else ""
        
        compliance_pct = (t_up / plan_up_sec * 100) if plan_up_sec > 0 else 0
        
        caption += f"\n\n📉 <b>План vs Факт:</b>\n"
        caption += f" • За планом світло: <b>{format_duration(plan_up_sec)}</b>\n"
        caption += f" • Реально світло: <b>{format_duration(t_up)}</b>\n"
        caption += f" • Відхилення: <b>{sign}{diff_hours:.1f}год</b> (Світла {compliance_pct:.0f}% від плану)"
               
    send_telegram_photo(filename, caption)
    
    if os.path.exists(filename):
        os.remove(filename)
