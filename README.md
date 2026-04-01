# FPREN — Florida Public Radio Emergency Network

A 24/7 automated weather radio broadcast system for Florida. FPREN ingests real-time NWS/IPAWS emergency alerts and ASOS weather observations, converts them to speech, and streams audio continuously over Icecast — providing automated emergency weather broadcasting for nine Florida zones.

> Research and development platform at the University of Florida.

---

## What It Does

- **Real-time alert ingestion** — NWS/IPAWS FL alerts polled every 2 minutes, deduplicated, stored in MongoDB
- **19-station ASOS observation network** — current conditions fetched every 15 minutes across Florida
- **Zone-based audio generation** — each of 9 FL zones gets its own alert audio (MP3 via Piper TTS)
- **Critical alert escalation** — tornado/hurricane warnings use ElevenLabs for broadcast-quality voice
- **AI broadcast scripting** — LiteLLM (llama-3.3-70b) rewrites alerts into on-air broadcast copy
- **Continuous Icecast stream** — FFmpeg feeds audio to Icecast at `http://128.227.67.234:8000/fpren`
- **Shiny monitoring dashboard** — live alert feed, obs, zone status at `http://128.227.67.234:3838`
- **Flask admin interface** — stream control, content upload, user management at port 5000

---

## Architecture

```
NWS / IPAWS / ASOS / FL511
          │
          ▼
   Fetcher Services (Python)
   ipaws_fetcher · weather_rss · extended_fetcher
          │
          ▼
       MongoDB
    weather_rss database
          │
          ├──────────────────────────────┐
          ▼                              ▼
  zone_alert_tts               Flask Admin (port 5000)
  (alert → MP3 per zone)       weather_rss/web/app.py
          │
          ▼
  audio/zones/<zone_id>/
          │
          ▼
  beacon-station-engine
  playlist · interrupt · scheduler
          │
          ▼
  icecast_streamer (FFmpeg)
          │
          ▼
  Icecast2 (port 8000)
  /fpren mount
          │
          ▼
  Shiny Dashboard (port 3838)
  shiny_dashboard/app.R
```

---

## Florida Coverage Zones

| Zone | Coverage |
|------|----------|
| All Florida | Statewide catch-all (major events only) |
| North Florida | Counties north of Marion |
| Central Florida | Marion → Palm Beach (Tampa, Daytona) |
| South Florida | Palm Beach south |
| Tampa | Hillsborough + Pinellas |
| Miami | Miami-Dade + Broward |
| Orlando | Orange + Osceola + Seminole |
| Jacksonville | Duval + Clay + St. Johns |
| Gainesville | Alachua |

**Monitored ASOS stations:**
`KGNV` `KOCF` `KPAK` `KJAX` `KTLH` `KPNS` `KECP` `KMCO` `KDAB` `KTPA` `KSRQ` `KLAL` `KRSW` `KFLL` `KMIA` `KPBI` `KEYW` `KSPG` `KAPF`

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Database | MongoDB (`weather_rss` db) |
| TTS — primary | Piper (`en_US-amy-medium`) |
| TTS — critical | ElevenLabs API |
| AI | UF LiteLLM (`llama-3.3-70b-instruct`) |
| Streaming | Icecast2 + FFmpeg |
| Web admin | Flask + Flask-Login |
| Dashboard | R Shiny |
| Proxy | Nginx |
| Audio processing | pydub, pedalboard, librosa |

---

## Repository Structure

```
Fpren-main/
├── weather_rss/          # Alert + obs fetchers, Flask admin
│   └── web/              # Flask admin dashboard (port 5000)
├── weather_station/      # Broadcast engine, TTS, Icecast
│   ├── core/             # Station loop, playlist, audio
│   └── services/         # Zone TTS, AI, Icecast streamer
├── shiny_dashboard/      # Shiny dashboard source (app.R)
├── mongo_tts/            # MongoDB TTS monitor service
├── scripts/              # Utilities (seed zones, monitor)
├── systemd/              # Systemd unit templates
└── reports/              # R report generation
```

---

## For Contributors / Developers

See [`CLAUDE.md`](CLAUDE.md) for the full technical context: service map, file ownership, common commands, gotchas, and environment variables.

Each subdirectory also has its own `CLAUDE.md`:
- [`weather_rss/CLAUDE.md`](weather_rss/CLAUDE.md) — fetcher pipeline
- [`weather_station/CLAUDE.md`](weather_station/CLAUDE.md) — broadcast engine
- [`shiny_dashboard/CLAUDE.md`](shiny_dashboard/CLAUDE.md) — dashboard deploy
- [`weather_rss/web/CLAUDE.md`](weather_rss/web/CLAUDE.md) — Flask admin API

---

## Security

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting.
