# CLAUDE.md — scripts/

One-off utilities, seed scripts, and background helpers for FPREN. None of these are part of
the main broadcast pipeline — they are run manually or by specific timers.

---

## Files

| File | Run how | Purpose |
|------|---------|---------|
| `seed_zone_definitions.py` | Manual (once) | Seeds `zone_definitions` collection with 9-zone config. Re-run after a DB reset. |
| `store_weather_history.R` | `fpren-weather-history.timer` (every 30 min) | Fetches current METAR obs for 16 FL cities, stores delta snapshots in `weather_history` (90-day TTL). |
| `waze_distance_helpers.R` | Source in RStudio | Waze CCP proximity helpers: `waze_alerts_near()`, `waze_jams_near()`, `waze_summary_near()` for BCP reports. |
| `fpren_snmp_agent.py` | Via `run_fpren_snmp.sh` | pass_persist script — exposes FPREN status via SNMP extend (invoked by snmpd, not directly). |
| `fpren_snmp_update.py` | `fpren-snmp-updater.timer` (every 60 s) | Reads FPREN system state (services, alerts, weather) and writes to `fpren_snmp_status` MongoDB collection. |
| `snmp_query.py` / `snmp_query.sh` | Manual | CLI helpers to read a single field from `fpren_snmp_status`. |
| `run_fpren_snmp.sh` | Called by snmpd | Shell wrapper around `fpren_snmp_agent.py` for snmpd pass_persist. |
| `check_fpren_access.py` | Manual | Smoke-test for dashboard/API reachability. |
| `cleanup_expired_invites.py` | `fpren-invite-cleanup.timer` (03:00 daily) | Removes expired invite tokens from `users` collection. |
| `network_monitor.py` | Manual | Checks connectivity to key external endpoints (FEMA, NWS, UF LiteLLM, ElevenLabs). |
| `stream_monitor.py` | Manual | Checks all 9 Icecast zone mount points for live sources. |
| `stream_notify.py` | Manual | Sends alert if Icecast stream drops. |
| `inovonics_poller.py` | Manual / optional | SNMP-based Inovonics FM receiver status poller (hardware integration). |
| `fetch_weather_rss.py` | Manual | Standalone test fetcher for weather_rss pipeline. |

---

## Common Commands

```bash
cd ~/Fpren-main && source venv/bin/activate

# Re-seed zone definitions (only needed after DB reset)
python3 scripts/seed_zone_definitions.py

# Check external endpoint reachability
python3 scripts/network_monitor.py

# Check all Icecast zone streams
python3 scripts/stream_monitor.py

# Query a single SNMP status field
python3 scripts/snmp_query.py systemHealth

# Clean up expired invites manually
python3 scripts/cleanup_expired_invites.py
```

---

## MongoDB Index Notes

- `weather_history` has a **90-day TTL** on `timestamp` — managed at DB level (added 2026-04-11 via mongosh).
  Do not re-create via `$run()` in `store_weather_history.R` — it already exists and would error.

---

## Gotchas

- `fpren_snmp_agent.py` is invoked by snmpd via `run_fpren_snmp.sh` — running it directly in a shell produces no useful output. Use `snmpwalk` to test.
- `waze_distance_helpers.R` requires the `waze_alerts` and `waze_jams` collections to be populated. The `beacon-waze-fetcher` service is installed but **disabled** — Waze CCP feed URL must be configured before enabling it (`weather_rss/config/waze_config.json` or `WAZE_FEED_URL` env var).
- `store_weather_history.R` writes delta snapshots — it skips a station if temperature/wind/visibility are within threshold of the last record. This keeps storage lean but means `weather_history` may have irregular intervals per station during stable weather.
