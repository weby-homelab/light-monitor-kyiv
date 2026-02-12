import json
import os
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
import sys
# Import necessary functions from the daily report script to reuse logic
from generate_daily_report import load_events, get_intervals_for_date, format_duration, TZ_OFFSET

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
EVENT_LOG_FILE = "event_log.json"
HISTORY_FILE = "schedule_history.json"

def get_schedule_slots(date_obj):
    try:
        if not os.path.exists(HISTORY_FILE):
            return None
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
        date_str = date_obj.strftime("%Y-%m-%d")
        return history.get(date_str)
    except:
        return None

def slots_to_intervals(slots):
    if not slots: return []
    intervals = []
    start_idx = 0
    current_state = slots[0]
    for i in range(1, len(slots)):
        if slots[i] != current_state:
            duration = (i - start_idx) * 0.5
            intervals.append((start_idx * 0.5, duration, current_state))
            current_state = slots[i]
            start_idx = i
    duration = (len(slots) - start_idx) * 0.5
    intervals.append((start_idx * 0.5, duration, current_state))
    return intervals

def get_weekly_stats(start_date, end_date, events):
    """
    Calculates stats for a specific range [start_date, end_date].
    Includes Plan vs Fact analysis.
    """
    total_up_sec = 0
    total_down_sec = 0
    total_plan_up = 0
    total_plan_down = 0
    
    days_stats = []
    
    current = start_date
    while current <= end_date:
        # --- Actual Data ---
        intervals = get_intervals_for_date(current, events)
        day_up = 0
        day_down = 0
        
        for start, end, state in intervals:
            duration = (end - start).total_seconds()
            if state == 'up' or state == 'unknown':
                day_up += duration
            elif state == 'down':
                day_down += duration
        
        # --- Planned Data ---
        slots = get_schedule_slots(current)
        if slots:
            # 48 slots, 0.5h each
            plan_up = sum(1 for s in slots if s) * 0.5
            plan_down = sum(1 for s in slots if not s) * 0.5
        else:
            plan_up, plan_down = 0, 0

        # --- Analysis ---
        # Diff: How many MORE hours of light we got than planned.
        # Positive = Good (More light), Negative = Bad (Less light)
        day_up_h = day_up / 3600
        diff = day_up_h - plan_up if slots else 0
        
        total_up_sec += day_up
        total_down_sec += day_down
        if slots:
            total_plan_up += plan_up
            total_plan_down += plan_down
            
        days_stats.append({
            'date': current,
            'up': day_up,
            'down': day_down,
            'plan_up': plan_up,
            'plan_down': plan_down,
            'diff': diff,
            'has_plan': bool(slots),
            'intervals': intervals
        })
        current += datetime.timedelta(days=1)
        
    # Sorting for insights
    sorted_by_outage = sorted(days_stats, key=lambda x: x['down'])
    
    # Filter days that actually had a plan for "Compliance" analysis
    days_with_plan = [d for d in days_stats if d['has_plan']]
    
    if days_with_plan:
        easiest_day = max(days_with_plan, key=lambda x: x['diff'])
        hardest_day = min(days_with_plan, key=lambda x: x['diff'])
    else:
        easiest_day = None
        hardest_day = None

    return {
        'total_up': total_up_sec,
        'total_down': total_down_sec,
        'total_plan_up': total_plan_up,
        'total_plan_down': total_plan_down,
        'best_day': sorted_by_outage[0], # Least actual outage
        'worst_day': sorted_by_outage[-1], # Most actual outage
        'easiest_day': easiest_day, # Best vs Plan
        'hardest_day': hardest_day, # Worst vs Plan
        'daily_data': days_stats
    }

def generate_weekly_chart(end_date, daily_data):
    # Set dark background style context for this chart
    with plt.style.context('dark_background'):
        # Increased vertical height for thicker bars (was 3.5, now 5.0)
        fig, ax = plt.subplots(figsize=(10, 5.0), facecolor='#2E2E2E')
        ax.set_facecolor('#2E2E2E')
        
        # Colors
        color_map = {'up': '#4CAF50', 'down': '#EF9A9A', 'unknown': '#C8E6C9'}
        sched_map = {True: '#FFF59D', False: '#BDBDBD'} # Yellow (Light), Gray (Outage)
        
        y_labels = []
        y_ticks = []
        
        dummy_date = datetime.date(2000, 1, 1)
        
        for i, day_info in enumerate(daily_data):
            day_date = day_info['date']
            intervals = day_info['intervals']
            
            # Position: 6 - i (Mon at top)
            y_pos = 6 - i
            
            day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
            label = f"{day_names[day_date.weekday()]} {day_date.strftime('%d.%m')}"
            y_labels.append(label)
            y_ticks.append(y_pos)
            
            # --- 1. Draw Actual Data (Top Strip) ---
            # Height 0.45, starting from y_pos
            
            for start, end, state in intervals:
                d_start = datetime.datetime.combine(dummy_date, start.time())
                d_end = datetime.datetime.combine(dummy_date, end.time())
                
                if end.time() == datetime.time.min and end != start:
                     d_end += datetime.timedelta(days=1)
                elif d_end < d_start:
                     d_end += datetime.timedelta(days=1)
                    
                start_num = mdates.date2num(d_start)
                end_num = mdates.date2num(d_end)
                duration_num = end_num - start_num
                
                color = color_map.get(state, '#C8E6C9')
                
                # Main bar (FACT): From y_pos to y_pos + 0.45 (Height 0.45)
                ax.broken_barh([(start_num, duration_num)], (y_pos, 0.45), facecolors=color, edgecolor='none')

            # --- 2. Draw Schedule Data (Bottom Strip) ---
            # Height 0.45, attached to bottom of main bar
            # From y_pos - 0.45 to y_pos
            
            slots = get_schedule_slots(day_date)
            if slots:
                sched_intervals = slots_to_intervals(slots)
                for start_h, duration_h, is_on in sched_intervals:
                    s_date = datetime.datetime.combine(dummy_date, datetime.time.min) + datetime.timedelta(hours=start_h)
                    start_n = mdates.date2num(s_date)
                    duration_n = duration_h / 24.0
                    
                    color = sched_map.get(is_on, '#E0E0E0')
                    ax.broken_barh([(start_n, duration_n)], (y_pos - 0.45, 0.45), facecolors=color, edgecolor='none')
            else:
                pass

        # Formatting
        # Tighter ylim for more compact look with thicker bars
        ax.set_ylim(-0.7, 6.7)
        ax.set_yticks(y_ticks)
        ax.set_yticklabels(y_labels, color='white')
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        
        # Remove Spines (Borders)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        # Bottom spine remains for timeline
        ax.spines['bottom'].set_color('white')
        
        x_start = datetime.datetime(2000, 1, 1, 0, 0)
        x_end = datetime.datetime(2000, 1, 1, 23, 59)
        ax.set_xlim(mdates.date2num(x_start), mdates.date2num(x_end))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        
        ax.set_title(f"Енергетичний тиждень ({daily_data[0]['date'].strftime('%d.%m')} - {daily_data[-1]['date'].strftime('%d.%m')})", fontsize=14, color='white')
        
        import matplotlib.patches as mpatches
        # Actual
        green_patch = mpatches.Patch(color='#4CAF50', label='Світло є')
        red_patch = mpatches.Patch(color='#EF9A9A', label='Світла немає')
        # Schedule
        yellow_patch = mpatches.Patch(color='#FFF59D', label='Графік: Є')
        gray_patch = mpatches.Patch(color='#BDBDBD', label='Графік: Немає')
        
        # Frameless legend at bottom
        legend = plt.legend(handles=[green_patch, red_patch, yellow_patch, gray_patch], 
                   loc='upper center', bbox_to_anchor=(0.5, -0.1),
                   fancybox=False, frameon=False, shadow=False, ncol=4)
        plt.setp(legend.get_texts(), color='white')
        
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.15)
        
        filename = f"weekly_report_{end_date.strftime('%Y-%m-%d')}.png"
        plt.savefig(filename, dpi=100, facecolor=fig.get_facecolor())
        plt.close()
    
    return filename

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(photo_path, 'rb') as f:
        files = {'photo': f}
        data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML', 'disable_notification': True}
        try:
            r = requests.post(url, files=files, data=data, timeout=20)
            if r.status_code == 200:
                print("Weekly report sent successfully.")
            else:
                print(f"Failed to send weekly report: {r.text}")
        except Exception as e:
            print(f"Error sending weekly report: {e}")

if __name__ == "__main__":
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ_OFFSET)))
    if len(sys.argv) > 1:
        target_date = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target_date = now.date()

    # Strict Week: Monday to Sunday
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    sunday = monday + datetime.timedelta(days=6)
        
    print(f"Generating weekly report for: {monday} to {sunday}...")
    
    events = load_events()
    stats = get_weekly_stats(monday, sunday, events)
    filename = generate_weekly_chart(sunday, stats['daily_data'])
    
    # Analysis
    up_h = stats['total_up'] / 3600
    down_h = stats['total_down'] / 3600
    plan_up_h = stats.get('total_plan_up', 0)
    plan_down_h = stats.get('total_plan_down', 0)
    
    total_h = up_h + down_h
    up_pct = (up_h / total_h * 100) if total_h > 0 else 0
    
    if up_pct > 90:
        verdict = "Тиждень був надзвичайно стабільним. Енергосистема працювала майже без обмежень."
    elif up_pct > 70:
        verdict = "Відносно спокійний тиждень. Відключення були прогнозованими та нетривалими."
    elif up_pct > 50:
        verdict = "Складний тиждень. Енергетики застосовували обмеження, але світло було більшу часть часу."
    else:
        verdict = "Важкий енергетичний тиждень. Тривалі відключення та дефіцит потужності в мережі."

    day_names = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]
    
    best_day = stats['best_day']
    worst_day = stats['worst_day']
    
    easiest = stats.get('easiest_day')
    hardest = stats.get('hardest_day')

    # Build Plan vs Fact section
    plan_section = ""
    if plan_up_h > 0:
        diff_total = up_h - plan_up_h
        sign = "+" if diff_total > 0 else ""
        compliance_pct = (up_h / plan_up_h * 100) if plan_up_h > 0 else 0
        
        plan_section = f"""
📉 <b>План vs Факт:</b>
 • За планом світло: <b>{int(plan_up_h)}год</b>
 • Реально світло: <b>{int(up_h)}год</b>
 • Відхилення: <b>{sign}{diff_total:.1f}год</b> (Світла {compliance_pct:.0f}% від плану)
"""
        # Add easier/harder days if we have variance
        if easiest and hardest and easiest != hardest:
             e_name = day_names[easiest['date'].weekday()]
             h_name = day_names[hardest['date'].weekday()]
             e_diff = easiest['diff']
             h_diff = hardest['diff']
             
             plan_section += f"\n🌤 <b>Легше ніж очікувалось:</b> {e_name} (+{e_diff:.1f}год світла)\n🌩 <b>Важче ніж очікувалось:</b> {h_name} ({h_diff:.1f}год світла)"

    caption = f"""📅 <b>Енергетичний тиждень ({monday.strftime('%d.%m')} - {sunday.strftime('%d.%m')})</b>

📊 <b>Загальні підсумки:</b>
 • Світло було: <b>{int(up_h)}год {int((up_h%1)*60)}хв</b> ({int(up_pct)}%)
 • Відключення: <b>{int(down_h)}год {int((down_h%1)*60)}хв</b>
 • В середньому без світла: <b>{int(down_h/7)}год {int(((down_h/7)%1)*60)}хв</b> на добу
{plan_section}

🏆 <b>Найменше відключень:</b> {day_names[best_day['date'].weekday()]}
🧟 <b>Найбільше відключень:</b> {day_names[worst_day['date'].weekday()]}

📝 <b>Аналіз:</b>
{verdict}

#тиждень #статистика_світла"""
    
    send_telegram_photo(filename, caption)
    if os.path.exists(filename):
        os.remove(filename)
