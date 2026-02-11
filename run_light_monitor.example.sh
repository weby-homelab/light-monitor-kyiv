#!/bin/bash
# Example run script. Copy to run_light_monitor.sh and fill in your details.
# Make sure to chmod +x run_light_monitor.sh

# If using .env file:
# source "/path/to/light-monitor-kyiv/.env"

# Or export variables directly (NOT RECOMMENDED for public repos):
export TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN_HERE"
export TELEGRAM_CHANNEL_ID="YOUR_CHANNEL_ID_HERE"

# Path to python in venv
VENV_PYTHON="./venv/bin/python"

# Change to project directory
cd "$(dirname "$0")"

# Run main script
$VENV_PYTHON main.py
