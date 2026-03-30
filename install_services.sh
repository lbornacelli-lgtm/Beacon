#!/bin/bash
# Installs all FPREN systemd services so they auto-start on boot

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="$SCRIPT_DIR/systemd"
UNIT_DIR="/etc/systemd/system"

echo "=== Installing FPREN systemd services ==="

SERVICES=(
    fpren-weather-service
    fpren-obs-fetcher
    fpren-ipaws-fetcher
    fpren-extended-fetcher
    fpren-alert-worker
    fpren-web-dashboard
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
echo "=== All FPREN services installed ==="
echo "Check status with:  sudo systemctl status fpren-weather-service"
echo "View logs with:     journalctl -u fpren-weather-service -f"
echo ""
echo "MongoDB (mongod) should already be enabled. If not, run:"
echo "  sudo systemctl enable mongod"
echo "  sudo systemctl start mongod"
