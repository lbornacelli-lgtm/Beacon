---
description: Check Icecast stream health and listener counts
allowed-tools: ["Bash"]
---

Check the health of the FPREN Icecast stream and report all relevant metrics.

Run each check and report results:

**1. Icecast server status:**
```bash
sudo systemctl is-active icecast2
```

**2. Stream mount status and listener counts:**
```bash
curl -s http://localhost:8000/status-json.xsl | python3 -m json.tool
```

**3. Check FFmpeg / stream feeder process:**
```bash
pgrep -a ffmpeg | grep fpren
```

**4. Icecast log (last 20 lines):**
```bash
sudo journalctl -u icecast2 -n 20 --no-pager
```

**5. Check if stream is reachable and returning audio data:**
```bash
curl -s -o /dev/null -w "Stream HTTP status: %{http_code}\n" --max-time 5 http://localhost:8000/fpren
```

**6. Broadcast engine status:**
```bash
sudo systemctl is-active beacon-station-engine zone-alert-tts
```

**7. Recent zone audio (last 3 generated files):**
```bash
mongosh weather_rss --quiet --eval "db.zone_alert_wavs.find({},{zone_id:1,filepath:1,created_at:1}).sort({created_at:-1}).limit(3).forEach(d => print(d.zone_id, d.filepath, d.created_at))"
```

Summarize the stream health: whether Icecast is up, whether the `/fpren` mount is active, current listener count, and whether the broadcast engine is feeding audio. Flag any issues found.
