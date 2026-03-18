#!/bin/bash
# Installs all Beacon systemd services so they auto-start on boot

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="$SCRIPT_DIR/systemd"
UNIT_DIR="/etc/systemd/system"

echo "=== Installing Beacon systemd services ==="

SERVICES=(
    beacon-weather-service
    beacon-obs-fetcher
    beacon-ipaws-fetcher
    beacon-extended-fetcher
    beacon-alert-worker
    beacon-web-dashboard
)

for svc in "${SERVICES[@]}"; do
    echo "[..] Installing $svc..."
    sudo cp "$SYSTEMD_DIR/$svc.service" "$UNIT_DIR/"
    sudo systemctl daemon-reload
    sudo systemctl enable "$svc"
    sudo systemctl start "$svc"
    echo "[OK] $svc installed and started"
done

echo ""
echo "=== All Beacon services installed ==="
echo "Check status with:  sudo systemctl status beacon-weather-service"
echo "View logs with:     journalctl -u beacon-weather-service -f"
echo ""
echo "MongoDB (mongod) should already be enabled. If not, run:"
echo "  sudo systemctl enable mongod"
echo "  sudo systemctl start mongod"
