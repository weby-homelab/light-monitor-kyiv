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

def get_weekly_stats(start_date, end_date, events):
    """
    Calculates stats for a specific range [start_date, end_date].
    """
    total_up_sec = 0
    total_down_sec = 0
    days_stats = []
    
    current = start_date
    while current <= end_date:
        intervals = get_intervals_for_date(current, events)
        day_up = 0
        day_down = 0
        
        for start, end, state in intervals:
            duration = (end - start).total_seconds()
            if state == 'up' or state == 'unknown':
                day_up += duration
            elif state == 'down':
                day_down += duration
        
        total_up_sec += day_up
        total_down_sec += day_down
        days_stats.append({
            'date': current,
            'up': day_up,
            'down': day_down,
            'intervals': intervals
        })
        current += datetime.timedelta(days=1)
        
    sorted_by_outage = sorted(days_stats, key=lambda x: x['down'])
    return {
        'total_up': total_up_sec,
        'total_down': total_down_sec,
        'best_day': sorted_by_outage[0],
        'worst_day': sorted_by_outage[-1],
        'daily_data': days_stats
    }

def generate_weekly_chart(end_date, daily_data):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    
    # Colors
    color_map = {'up': '#4CAF50', 'down': '#F44336', 'unknown': '#C8E6C9'}
    
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
            # Consistent clean bars
            ax.broken_barh([(start_num, duration_num)], (y_pos - 0.35, 0.7), facecolors=color, edgecolor='none')

    # Formatting
    ax.set_ylim(-0.5, 6.5)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_labels)
    
    # Remove Spines (Borders)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    # Bottom spine remains for timeline
    
    x_start = datetime.datetime(2000, 1, 1, 0, 0)
    x_end = datetime.datetime(2000, 1, 1, 23, 59)
    ax.set_xlim(mdates.date2num(x_start), mdates.date2num(x_end))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    
    ax.set_title(f"Енергетичний тиждень ({daily_data[0]['date'].strftime('%d.%m')} - {daily_data[-1]['date'].strftime('%d.%m')})", fontsize=14)
    
    import matplotlib.patches as mpatches
    green_patch = mpatches.Patch(color='#4CAF50', label='Світло є')
    pale_patch = mpatches.Patch(color='#C8E6C9', label='Світло (ймовірно)')
    red_patch = mpatches.Patch(color='#F44336', label='Відключення')
    
    # Frameless legend at bottom
    plt.legend(handles=[green_patch, pale_patch, red_patch], 
               loc='upper center', bbox_to_anchor=(0.5, -0.1),
               fancybox=False, frameon=False, shadow=False, ncol=3)
    
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.15)
    
    filename = f"weekly_report_{end_date.strftime('%Y-%m-%d')}.png"
    plt.savefig(filename, dpi=100)
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
    best_name = day_names[stats['best_day']['date'].weekday()]
    worst_name = day_names[stats['worst_day']['date'].weekday()]

    caption = f"""📅 <b>Енергетичний тиждень ({monday.strftime('%d.%m')} - {sunday.strftime('%d.%m')})</b>

📊 <b>Підсумки:</b>
 • Світло було: <b>{int(up_h)}год {int((up_h%1)*60)}хв</b> ({int(up_pct)}%)
 • Відключення: <b>{int(down_h)}год {int((down_h%1)*60)}хв</b>
 • В середньому без світла: <b>{int(down_h/7)}год {int(((down_h/7)%1)*60)}хв</b> на добу

🏆 <b>Найкращий день:</b> {best_name}
🧟 <b>Найважчий день:</b> {worst_name}

📝 <b>Аналіз:</b>
{verdict}

#тиждень #статистика_світла"""
    
    send_telegram_photo(filename, caption)
    if os.path.exists(filename):
        os.remove(filename)