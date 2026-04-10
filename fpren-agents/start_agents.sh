#!/bin/bash
FPREN_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$FPREN_DIR/logs"
VENV="$FPREN_DIR/../venv/bin/python3"
mkdir -p "$LOG_DIR"
echo "=== FPREN Agent System Startup ==="
echo "[..] Starting director_with_r..."
nohup $VENV "$FPREN_DIR/director_with_r.py" > "$LOG_DIR/director.log" 2>&1 &
echo $! > "$LOG_DIR/director.pid"
echo "[OK] Director (PID $!) → $LOG_DIR/director.log"
echo ""
echo "Stop with: kill \$(cat $LOG_DIR/director.pid)"
