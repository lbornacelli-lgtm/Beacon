#!/bin/bash
# Beacon Weather Station - Native Service Launcher
# Starts MongoDB and all weather_rss fetcher services

BEACON_DIR="$(cd "$(dirname "$0")" && pwd)"
WEATHER_RSS="$BEACON_DIR/weather_rss"
LOG_DIR="$WEATHER_RSS/logs"
MONGO_DBPATH="/var/lib/mongodb"
MONGO_LOG="/var/log/mongodb/mongod.log"

mkdir -p "$LOG_DIR"

echo "=== Beacon Weather Station Startup ==="

# --- MongoDB ---
if mongosh --eval "db.runCommand({ping:1})" --quiet > /dev/null 2>&1; then
    echo "[OK] MongoDB already running"
else
    echo "[..] Starting MongoDB..."
    mongod --dbpath "$MONGO_DBPATH" --logpath "$MONGO_LOG" --fork
    sleep 2
    echo "[OK] MongoDB started"
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
start_service "web_dashboard"      "web/app.py"

echo ""
echo "=== All services started ==="
echo "Web dashboard: http://localhost:5000"
echo "Logs: $LOG_DIR"
echo ""
echo "To stop all services, run: $BEACON_DIR/stop_services.sh"
