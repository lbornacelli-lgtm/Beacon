#!/bin/bash
LOGFILE=/home/ufuser/weather_rss/logs/gui.log
echo "Starting Weather RSS GUI at $(date)" >> "$LOGFILE"

export DISPLAY=:0
export XAUTHORITY=/home/ufuser/.Xauthority

/home/ufuser/weather_rss/venv/bin/python /home/ufuser/weather_rss/weather_rss_gui.py >> "$LOGFILE" 2>&1
