---
description: Show status of all FPREN services in one view
allowed-tools: ["Bash"]
---

Show the status of every FPREN service in a single consolidated view.

Run these commands and present the results clearly grouped by category:

**Core services:**
```bash
sudo systemctl is-active beacon-station-engine beacon-web-dashboard zone-alert-tts beacon-ipaws-fetcher beacon-obs-fetcher beacon-extended-fetcher beacon-mongo-tts icecast2 shiny-server mongod nginx
```

**Stream health (Icecast):**
```bash
curl -s http://localhost:8000/status-json.xsl | python3 -m json.tool 2>/dev/null | grep -E "listeners|mount|title|server_name" | head -20
```

**Flask web dashboard:**
```bash
curl -s -o /dev/null -w "Flask admin HTTP status: %{http_code}\n" http://localhost:5000/
```

**MongoDB alert counts:**
```bash
mongosh weather_rss --quiet --eval "print('Active alerts:', db.nws_alerts.countDocuments()); print('Zone audio records:', db.zone_alert_wavs.countDocuments()); print('Zones defined:', db.zone_definitions.countDocuments())"
```

**Recent zone audio (last 5 files generated):**
```bash
mongosh weather_rss --quiet --eval "db.zone_alert_wavs.find({},{zone_id:1,alert_id:1,created_at:1}).sort({created_at:-1}).limit(5).forEach(d => print(d.zone_id, '|', d.alert_id, '|', d.created_at))"
```

Format the output as a clean status report with clear section headers.
