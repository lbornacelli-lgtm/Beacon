---
description: Restart all FPREN services in the correct dependency order
allowed-tools: ["Bash"]
---

Restart all FPREN services in the correct order (dependencies first). Report the active/failed status of each service after restarting.

**Restart order:**
1. MongoDB (database — everything else depends on it)
2. Icecast (streaming server — broadcast engine depends on it)
3. Fetcher services (populate the database)
4. Zone alert TTS (reads DB, writes audio)
5. Broadcast engine (reads audio, streams to Icecast)
6. Flask admin dashboard (reads DB and engine state)
7. Shiny dashboard (reads DB)
8. Nginx (proxy — restart last)

Run each step, wait briefly for services to settle, then confirm status:

```bash
sudo systemctl restart mongod && sleep 2
sudo systemctl restart icecast2 && sleep 2
sudo systemctl restart beacon-ipaws-fetcher beacon-obs-fetcher beacon-extended-fetcher && sleep 2
sudo systemctl restart zone-alert-tts beacon-mongo-tts && sleep 2
sudo systemctl restart beacon-station-engine && sleep 3
sudo systemctl restart beacon-web-dashboard && sleep 2
sudo systemctl restart shiny-server && sleep 2
sudo systemctl restart nginx && sleep 1
```

Then show final status of all services:
```bash
sudo systemctl is-active mongod icecast2 beacon-ipaws-fetcher beacon-obs-fetcher beacon-extended-fetcher zone-alert-tts beacon-mongo-tts beacon-station-engine beacon-web-dashboard shiny-server nginx
```

Report any services that are not `active` with their journal output:
```bash
sudo journalctl -u <failed-service> -n 20 --no-pager
```
