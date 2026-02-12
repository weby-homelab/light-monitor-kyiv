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
    Returns None if no schedule is found.
    """
    if not os.path.exists(SCHEDULE_FILE):
        return None
        
    try:
        with open(SCHEDULE_FILE, 'r') as f:
            data = json.load(f)
            
        # Priority: Yasno -> Github
        source = data.get('yasno') or data.get('github')
        if not source: return None
        
        # Get the first group (assuming one group per config)
        group_key = list(source.keys())[0]
        schedule_data = source[group_key]
        
        date_str = target_date.strftime("%Y-%m-%d")
        
        if date_str in schedule_data and schedule_data[date_str].get('slots'):
             return schedule_data[date_str]['slots']
             
        return None
            
    except Exception as e:
        print(f"Error loading schedule: {e}")
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
    # Dark Mode
    with plt.style.context('dark_background'):
        # Reduced height again (was 2.4, now 2.0 -> thinner bars)
        fig, ax = plt.subplots(figsize=(10, 2.0), facecolor='#2E2E2E')
        ax.set_facecolor('#2E2E2E')
        
        # Define geometries - Glued together
        # Schedule (Bottom): y=12.5, h=2.5 (ends at 15)
        # Separator: at y=15
        # Actual (Top): y=15, h=2.5 (starts at 15)
        
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

        # --- Separator Line ---
        # Placed exactly at the boundary (y=15). White for visibility.
        ax.axhline(y=15, color='white', linewidth=0.8, zorder=3)

        # --- Actual Data (Top Bar) ---
        # User requested 'unknown' (past gaps) to be Pale Green and counted as Light.
        color_map = {'up': '#4CAF50', 'down': '#EF9A9A', 'unknown': '#C8E6C9'} # Unknown = Pale Green
        
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
                # User implies unknown gaps are light
                total_up += duration_sec
                
            color = color_map.get(state, '#C8E6C9')
            
            start_num = mdates.date2num(start)
            end_num = mdates.date2num(end)
            duration_num = end_num - start_num
            
            ax.broken_barh([(start_num, duration_num)], (act_y, act_h), facecolors=color, edgecolor='none')
            
            if end > last_actual_end:
                last_actual_end = end

        # --- Future Bar ---
        # If the last actual interval ends before day_end, draw "future" bar
        if last_actual_end < day_end:
            start_num = mdates.date2num(last_actual_end)
            end_num = mdates.date2num(day_end)
            duration_num = end_num - start_num
            
            # Dark grey for "future/unknown" in dark mode
            ax.broken_barh([(start_num, duration_num)], (act_y, act_h), facecolors='#424242', edgecolor='#757575', hatch='///', linewidth=0.5)

        # --- Formatting ---
        # Set ylim to fit bars comfortably
        ax.set_ylim(11, 19) 
        ax.set_xlim(mdates.date2num(day_start), mdates.date2num(day_end))
        
        # Remove spines (borders)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        # Bottom spine remains (timeline), make it white
        ax.spines['bottom'].set_color('white')
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))))
        
        # Colors for ticks
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        
        # Custom Y labels - centered on bars
        ax.set_yticks([sched_y + sched_h/2, act_y + act_h/2])
        ax.set_yticklabels(['Графік', 'Факт'], color='white')
        
        ax.set_title(f"Статистика світла за {target_date.strftime('%d.%m.%Y')}", fontsize=12, color='white')
        
        # --- Legend ---
        import matplotlib.patches as mpatches
        
        # Actual
        green_patch = mpatches.Patch(color='#4CAF50', label=f'Світло є')
        red_patch = mpatches.Patch(color='#EF9A9A', label=f'Світла немає')
        
        # Schedule
        yellow_patch = mpatches.Patch(color='#FFF59D', label='Графік: Є')
        gray_patch = mpatches.Patch(color='#BDBDBD', label='Графік: Немає')
        
        # Single row (ncol=4), adjusted bottom margin
        legend = plt.legend(handles=[green_patch, red_patch, yellow_patch, gray_patch], 
                   loc='upper center', bbox_to_anchor=(0.5, -0.25),
                   fancybox=False, frameon=False, shadow=False, ncol=4, fontsize='small') # Removed legend frame too
        plt.setp(legend.get_texts(), color='white')

        plt.tight_layout()
        # Adjust layout to make room for legend at bottom
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
        # Show unknown for the whole day if no data
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
        calc_end = now if target_date == now.date() else day_start + datetime.timedelta(hours=24)
        intervals = [(day_start, calc_end, "unknown")]

    filename, t_up, t_down = generate_chart(target_date, intervals, sched_intervals)
    
    caption = (f"📊 <b>Звіт за {target_date.strftime('%d.%m.%Y')}</b>\n\n"
               f"💡 Світло було: <b>{format_duration(t_up)}</b>\n"
               f"❌ Світла не було: <b>{format_duration(t_down)}</b>")
               
    send_telegram_photo(filename, caption)
    
    if os.path.exists(filename):
        os.remove(filename)
