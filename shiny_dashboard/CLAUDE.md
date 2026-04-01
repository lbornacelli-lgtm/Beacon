# CLAUDE.md — shiny_dashboard

This directory contains the Shiny app source for the FPREN primary monitoring dashboard. See root [`CLAUDE.md`](../CLAUDE.md) for system context.

---

## Critical: How Shiny Deploys

**Editing `shiny_dashboard/app.R` does NOT update the live dashboard.**

The live dashboard is served from a separate deployed path:
```
/srv/shiny-server/fpren/app.R   ← what Shiny Server actually reads
```

You must copy and restart after every edit:

```bash
sudo cp shiny_dashboard/app.R /srv/shiny-server/fpren/app.R
sudo chown shiny:shiny /srv/shiny-server/fpren/app.R
sudo systemctl restart shiny-server
```

Or use the slash command: `/deploy-shiny`

---

## What the Dashboard Shows

- Live NWS alert feed (reads `nws_alerts` MongoDB collection)
- Current obs for 19 FL ASOS stations (reads `feed_status`)
- Zone audio status (reads `zone_alert_wavs`)
- Icecast stream status
- Service health indicators
- FL traffic incidents (reads `fl_traffic`)

---

## Access

| URL | Port | Auth |
|-----|------|------|
| `http://128.227.67.234` | 80 → 3838 via Nginx | None (public) |
| `http://128.227.67.234:3838` | 3838 | None (direct) |

Port 80 is pending UF IT firewall approval — direct port 3838 access works now.

---

## Shiny Server Config

Shiny Server is installed system-wide. Config: `/etc/shiny-server/shiny-server.conf`
App path: `/srv/shiny-server/fpren/`

```bash
# Service management
sudo systemctl status shiny-server
sudo systemctl restart shiny-server

# View Shiny logs
sudo journalctl -u shiny-server -f
tail -f /var/log/shiny-server/*.log
```

---

## R Dependencies

The app uses standard Shiny + tidyverse packages plus `mongolite` for MongoDB access. If adding new packages:

```r
# Install on the server (run once)
sudo Rscript -e "install.packages('package_name', repos='https://cran.rstudio.com/')"
```

**RStudio Server** is available at `http://128.227.67.234:8787` for interactive R development.

---

## Gotchas

- Always deploy after editing — Shiny Server reads from `/srv/shiny-server/fpren/`, not from the repo.
- The `shiny` user must own the deployed file — always `chown shiny:shiny` after copy.
- MongoDB connection in `app.R` uses `mongolite` — it connects to `mongodb://localhost:27017/weather_rss`.
- If the dashboard shows a grey screen, check Shiny logs: `sudo journalctl -u shiny-server -f`.
