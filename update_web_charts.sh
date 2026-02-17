#!/bin/bash

# Move to the correct directory
cd /root/geminicli/light-monitor-kyiv

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Run daily report generation (updates chart.png, stats.json, and Telegram message)
./venv/bin/python generate_daily_report.py

# Run weekly report generation for web ONLY (updates weekly.png)
./venv/bin/python generate_weekly_report.py --output web/weekly.png
