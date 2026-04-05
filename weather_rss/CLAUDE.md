# CLAUDE.md — weather_rss (Fetcher Pipeline)

This directory contains all data ingestion services that pull weather data from external sources and write it to MongoDB. See the root [`CLAUDE.md`](../CLAUDE.md) for overall system context.

---

## Fetcher Services

| Service name | File | Schedule | Writes to |
|---|---|---|---|
| `beacon-ipaws-fetcher` | `ipaws_fetcher.py` | Every 2 min | `nws_alerts` |
| `beacon-obs-fetcher` | `weather_rss.py` | Every 15 min | `feed_status` |
| `beacon-extended-fetcher` | `extended_fetcher.py` | Every 30 min | `fl_traffic`, extended forecast fields |
| `beacon-airport-delays` | `airport_delays_fetcher.py` | Periodic | FAA delay data |
| `beacon-mongo-tts` | `../mongo_tts/app.py` | Continuous | Reads `nws_alerts`, triggers TTS |
| `beacon-rivers-fetcher` | `fl_rivers_fetcher.py` | Every 15 min | `fl_river_gauges`, `fl_river_readings` |
| `beacon-rivers-agent` | `fl_rivers_agent.py` | Every 1 hr | `fl_river_alerts` (LiteLLM agent) |

---

## Data Sources

| Source | URL/API | What it provides |
|--------|---------|-----------------|
| NWS/IPAWS | `https://api.weather.gov/alerts/active?area=FL` | Active FL weather alerts |
| ASOS (19 stations) | NWS XML feeds | Current obs: temp, wind, precip, sky |
| NHC RSS | `nhc.noaa.gov` feeds | Atlantic + East Pacific tropical weather |
| FL511 | FL511 traffic API | Traffic incidents statewide |
| FAA ATCSCC | FAA XML API | Airport delay programs |

**19 monitored ASOS stations:**
`KGNV` `KOCF` `KPAK` `KJAX` `KTLH` `KPNS` `KECP` `KMCO` `KDAB` `KTPA` `KSRQ` `KLAL` `KRSW` `KFLL` `KMIA` `KPBI` `KEYW` `KSPG` `KAPF`

---

## MongoDB Collections Written Here

| Collection | Schema highlights |
|------------|------------------|
| `nws_alerts` | `alert_id`, `event`, `headline`, `area_desc`, `counties` (list), `severity`, `urgency`, `expires`, `processed` flag |
| `feed_status` | `station_id`, `obs_time`, `temp_f`, `wind_mph`, `wind_dir`, `sky`, `fetched_at` |
| `fl_traffic` | `incident_id`, `description`, `county`, `lat`, `lon`, `fetched_at` |
| `zone_alert_wavs` | `zone_id`, `alert_id`, `filepath`, `created_at` — written by `zone_alert_tts.py` |
| `zone_definitions` | `zone_id`, `counties` (list), `cleanup_hours`, `max_files` — seeded by script |

---

## Alert Deduplication

`ipaws_fetcher.py` deduplicates by `alert_id`. An alert is only inserted if it does not already exist in `nws_alerts`. Expired alerts are not deleted automatically — the broadcast engine reads `expires` to skip them.

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `ipaws_fetcher.py` | Primary NWS/IPAWS alert ingestion |
| `weather_rss.py` | ASOS obs fetcher for 19 FL stations |
| `extended_fetcher.py` | Extended forecasts + FL511 traffic data |
| `airport_delays_fetcher.py` | FAA airport delay programs |
| `alert_worker.py` | Alert rule processor (triggers actions on new alerts) |
| `alert_player.py` | Local audio playback trigger (legacy) |
| `check_feeds.py` | Feed health checker utility |
| `weather_health.py` | Service health reporter |
| `email_utils.py` | Shared email utilities |
| `config/smtp_config.json` | SMTP email settings (edited via Flask admin) |
| `web/` | Flask admin dashboard — see `web/CLAUDE.md` |

---

## Common Commands

```bash
# Check fetcher logs
sudo journalctl -u beacon-ipaws-fetcher.service -f
sudo journalctl -u beacon-obs-fetcher.service -f

# Restart a fetcher
sudo systemctl restart beacon-ipaws-fetcher
sudo systemctl restart beacon-obs-fetcher

# Verify alerts are coming in
mongosh weather_rss --eval "db.nws_alerts.find({},{event:1,area_desc:1,expires:1}).limit(5).toArray()"

# Check feed status
mongosh weather_rss --eval "db.feed_status.find().sort({fetched_at:-1}).limit(3).toArray()"

# Manually run ipaws fetcher (for testing)
cd ~/Fpren-main && source venv/bin/activate
python3 weather_rss/ipaws_fetcher.py
```

---

## Gotchas

- `alert_worker.py` processes alerts from MongoDB — it does **not** fetch them. The fetchers are separate services.
- SMTP config is stored in `config/smtp_config.json` and editable via the Flask admin UI. Do not hardcode credentials.
- The `weather_rss.py` obs fetcher writes to `feed_status`, not `nws_alerts`. Different collections.
- FL511 traffic data requires the FL511 API key set in environment — check `extended_fetcher.py` if traffic data is missing.
