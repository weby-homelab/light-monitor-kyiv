#!/bin/bash
cd /root/geminicli/light-monitor-kyiv

# Load environment variables from .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

./venv/bin/python main.py "$@"
