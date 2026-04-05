#!/usr/bin/env bash
# install_rivers.sh — Install and start FPREN Florida Rivers services
set -e
cd "$(dirname "$0")/.."

echo "[1/4] Copying systemd units..."
sudo cp systemd/beacon-rivers-fetcher.service /etc/systemd/system/
sudo cp systemd/beacon-rivers-agent.service   /etc/systemd/system/

echo "[2/4] Reloading systemd..."
sudo systemctl daemon-reload

echo "[3/4] Enabling and starting services..."
sudo systemctl enable --now beacon-rivers-fetcher
sudo systemctl enable --now beacon-rivers-agent

echo "[4/4] Running initial river fetch (may take 20–30 s)..."
source venv/bin/activate
python3 weather_rss/fl_rivers_fetcher.py

echo ""
echo "✓ Florida Rivers services installed."
echo ""
echo "Quick checks:"
echo "  sudo systemctl status beacon-rivers-fetcher"
echo "  sudo systemctl status beacon-rivers-agent"
echo "  sudo journalctl -u beacon-rivers-fetcher -f"
echo "  mongosh weather_rss --eval \"db.fl_river_gauges.countDocuments()\""
echo "  mongosh weather_rss --eval \"db.fl_river_readings.countDocuments()\""
