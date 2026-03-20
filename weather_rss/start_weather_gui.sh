#!/bin/bash
LOGFILE=/home/ufuser/Fpren-main/weather_rss/logs/gui.log
echo "Starting Weather RSS GUI at $(date)" >> "$LOGFILE"

export DISPLAY=:0
export XAUTHORITY=/home/ufuser/Fpren-main/.Xauthority

/home/ufuser/Fpren-main/weather_rss/venv/bin/python /home/ufuser/Fpren-main/weather_rss/weather_rss_gui.py >> "$LOGFILE" 2>&1
