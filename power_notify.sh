#!/bin/bash

# Configuration
source "/root/geminicli/light-monitor-kyiv/.env"
TOKEN="$TELEGRAM_BOT_TOKEN"
CHAT_ID="$TELEGRAM_CHANNEL_ID"
STATE_FILE="/root/geminicli/light-monitor-kyiv/power.state"
TZ="Europe/Kyiv"

# Current time
NOW=$(date +%s)
TIME=$(date +"%H:%M")

# Load state if exists
if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
fi

# Function to calculate duration
calc_duration() {
    local start=$1
    local end=$2
    local diff=$((end - start))
    
    if [ $diff -lt 0 ]; then diff=0; fi
    
    local H=$((diff / 3600))
    local M=$(((diff % 3600) / 60))
    
    echo "${H}год ${M}хв"
}

# Function to send Telegram message
send_msg() {
    local message="$1"
    /usr/bin/curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
        -d chat_id="$CHAT_ID" \
        -d parse_mode="HTML" \
        --data-urlencode "text=$message" > /dev/null
}

case "$1" in
    up)
        # Check if we were actually down
        if [ -z "$LAST_HEARTBEAT" ]; then
            LAST_HEARTBEAT=$((NOW - 3600)) 
        fi

        DURATION=$(calc_duration "$LAST_HEARTBEAT" "$NOW")
        
        # Message
        MSG="🟢 <b>$TIME Світло з'явилося</b>

📊 <b>Статистика відключення:</b>
• Світла не було: <code>$DURATION</code>

🗓 <b>Аналіз:</b>
• Наступне планове: <i>Див. графік</i>"

        send_msg "$MSG"
        
        # Update state: We are UP now.
        echo "START_TIME=$NOW" > "$STATE_FILE"
        echo "LAST_HEARTBEAT=$NOW" >> "$STATE_FILE"
        ;;

    down)
        # Graceful shutdown trigger
        if [ -z "$START_TIME" ]; then
            START_TIME=$((NOW - 3600)) 
        fi
        
        DURATION=$(calc_duration "$START_TIME" "$NOW")
        
        MSG="🔴 <b>$TIME Світло зникло!</b>

📊 <b>Статистика сесії:</b>
• Світло було: <code>$DURATION</code>

🗓 <b>Прогноз:</b>
• Очікуємо за графіком: <i>Див. графік</i>"

        send_msg "$MSG"
        
        # Note: We don't update state here because the file might be wiped on reboot 
        # depending on location, but mainly because 'beat' handles the last alive time.
        ;;

    beat)
        # Heartbeat - run this via cron every minute
        # It preserves the START_TIME but updates LAST_HEARTBEAT
        if [ -f "$STATE_FILE" ]; then
            # Keep existing START_TIME, only update heartbeat
            grep "START_TIME=" "$STATE_FILE" > "$STATE_FILE.tmp"
            echo "LAST_HEARTBEAT=$NOW" >> "$STATE_FILE.tmp"
            mv "$STATE_FILE.tmp" "$STATE_FILE"
        else
            # Initialize if missing
            echo "START_TIME=$NOW" > "$STATE_FILE"
            echo "LAST_HEARTBEAT=$NOW" >> "$STATE_FILE"
        fi
        ;;

    *)
        echo "Usage: $0 {up|down|beat}"
        exit 1
        ;;
esac
