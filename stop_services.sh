#!/bin/bash
BEACON_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BEACON_DIR/weather_rss/logs"

echo "=== Stopping FPREN Weather Station Services ==="

for pidfile in "$LOG_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    name=$(basename "$pidfile" .pid)
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        echo "[..] Stopping $name (PID $pid)..."
        kill "$pid"
        echo "[OK] $name stopped"
    else
        echo "[--] $name was not running"
    fi
    rm -f "$pidfile"
done

echo "[..] Stopping MongoDB..."
sudo systemctl stop mongod && echo "[OK] MongoDB stopped" || echo "[--] MongoDB was not running"
echo "=== Done ==="
