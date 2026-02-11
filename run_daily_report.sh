#!/bin/bash
# Load environment if needed, but we hardcode for now as in run_light_monitor.sh
export TELEGRAM_BOT_TOKEN="7103787639:AAEMtoJmQnWMPcOfNQHAS_sWnlna9X6JqvU"
export TELEGRAM_CHANNEL_ID="-1002213828913"

cd /root/geminicli/light-monitor-kyiv
./venv/bin/python generate_daily_report.py >> cron.log 2>&1
