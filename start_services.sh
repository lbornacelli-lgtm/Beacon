#!/bin/bash
# FPREN Weather Station - Native Service Launcher
# Starts MongoDB and all weather_rss fetcher services

BEACON_DIR="$(cd "$(dirname "$0")" && pwd)"
WEATHER_RSS="$BEACON_DIR/weather_rss"
LOG_DIR="$WEATHER_RSS/logs"
MONGO_DBPATH="/var/lib/mongodb"
MONGO_LOG="/var/log/mongodb/mongod.log"

mkdir -p "$LOG_DIR"

echo "=== FPREN Weather Station Startup ==="

# --- MongoDB ---
if mongosh --eval "db.runCommand({ping:1})" --quiet > /dev/null 2>&1; then
    echo "[OK] MongoDB already running"
else
    echo "[..] Starting MongoDB via systemctl..."
    sudo systemctl start mongod
    sleep 3
    if mongosh --eval "db.runCommand({ping:1})" --quiet > /dev/null 2>&1; then
        echo "[OK] MongoDB started"
    else
        echo "[ERROR] MongoDB failed to start — check: sudo journalctl -u mongod -n 20"
        exit 1
    fi
fi

# --- Weather RSS Fetcher Services ---
cd "$WEATHER_RSS"
export MONGO_URI="mongodb://localhost:27017"

start_service() {
    local name="$1"
    local script="$2"
    local logfile="$LOG_DIR/${name}.log"
    echo "[..] Starting $name..."
    nohup /home/ufuser/Fpren-main/venv/bin/python3 "$script" > "$logfile" 2>&1 &
    echo $! > "$LOG_DIR/${name}.pid"
    echo "[OK] $name started (PID $!), logging to $logfile"
}

start_service "weather_service"    "weather_service.py"
start_service "obs_fetcher"        "weather_rss.py"
start_service "ipaws_fetcher"      "ipaws_fetcher.py"
start_service "extended_fetcher"   "extended_fetcher.py"
start_service "alert_worker"       "alert_worker.py"

# --- Weather Station Alert TTS Service ---
echo "[..] Starting alert_service (TTS WAV generator)..."
cd /home/ufuser/Fpren-main/weather_station
nohup /home/ufuser/Fpren-main/venv/bin/python3 run_alert_service.py >> /home/ufuser/Fpren-main/weather_station/logs/alert_service.log 2>&1 &
echo $! > /home/ufuser/Fpren-main/weather_rss/logs/alert_service.pid
echo "[OK] alert_service started (PID $!)"
cd /home/ufuser/Fpren-main/weather_rss
start_service "web_dashboard"      "web/app.py"

# --- Alarm & Monitoring System ---
echo "[..] Starting alarm system services..."
ALARM_SYS="$BEACON_DIR/fpren-agents/alarm_system"

nohup /home/ufuser/Fpren-main/venv/bin/python3 "$ALARM_SYS/alarm_engine.py" \
    > "$LOG_DIR/alarm_engine.log" 2>&1 &
echo $! > "$LOG_DIR/alarm_engine.pid"
echo "[OK] alarm_engine started (PID $!)"

nohup /home/ufuser/Fpren-main/venv/bin/python3 "$ALARM_SYS/watchdogs/stream_watchdog.py" \
    > "$LOG_DIR/stream_watchdog.log" 2>&1 &
echo $! > "$LOG_DIR/stream_watchdog.pid"
echo "[OK] stream_watchdog started (PID $!)"

nohup /home/ufuser/Fpren-main/venv/bin/python3 "$ALARM_SYS/watchdogs/feed_watchdog.py" \
    > "$LOG_DIR/feed_watchdog.log" 2>&1 &
echo $! > "$LOG_DIR/feed_watchdog.pid"
echo "[OK] feed_watchdog started (PID $!)"

nohup /home/ufuser/Fpren-main/venv/bin/python3 "$ALARM_SYS/snmp_engine/snmp_poller.py" \
    > "$LOG_DIR/snmp_poller.log" 2>&1 &
echo $! > "$LOG_DIR/snmp_poller.pid"
echo "[OK] snmp_poller started (PID $!)"

echo ""
echo "=== All services started ==="
echo "Web dashboard:    http://localhost:5000"
echo "Alarm dashboard:  http://localhost:5000/alarms"
echo "Logs: $LOG_DIR"
echo ""
echo "To stop all services, run: $BEACON_DIR/stop_services.sh"
