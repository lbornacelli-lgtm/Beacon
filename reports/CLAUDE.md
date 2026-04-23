# CLAUDE.md — reports/

R Markdown templates and generation scripts for FPREN automated reports. Reports are rendered
by `rmarkdown::render()` from R and typically produce PDF output.

---

## Report Templates (`.Rmd`)

| File | Purpose | Triggered by |
|------|---------|-------------|
| `business_continuity_report.Rmd` | BCP report for a user asset — weather risk, alerts, traffic, census demographics, emergency contacts | Flask `/api/reports/generate-bcp` + Shiny Reports tab |
| `fpren_comprehensive_project_report.Rmd` | Full system status: all zones, weather, alerts, streams | `fpren-comprehensive-2pm.timer` (daily 2 PM ET) |
| `fpren_alert_report.Rmd` | Alert summary for a date range | `fpren-report.timer` (06:00 daily) |
| `weather_trends_report.Rmd` | Weather trends for 16 FL cities from `weather_history` | Manual / 2PM report |
| `traffic_analysis_report.Rmd` | FL511 traffic incident analysis by county | Manual |
| `census_impact_report.Rmd` | Census vulnerability + active alert population impact | Manual / BCP integration |
| `county_alerts_report.Rmd` | Alert history per county | Manual |
| `fpren_accessibility_report.Rmd` | Accessibility metrics for FPREN broadcast coverage | Manual |
| `fpren_uf_it_report.Rmd` | UF IT/network status summary (for UF IT meetings) | Manual |
| `bcp_broadcast_facility.Rmd` | BCP template variant for broadcast facilities | Flask `/api/reports/generate-bcp` |
| `bcp_campus_police.Rmd` | BCP template variant for campus police | Flask `/api/reports/generate-bcp` |
| `bcp_county_em.Rmd` | BCP template variant for county emergency management | Flask `/api/reports/generate-bcp` |

---

## Generation Scripts (`.R`)

| File | Purpose |
|------|---------|
| `generate_comprehensive_2pm.R` | Entry point for daily 2 PM report — renders `fpren_comprehensive_project_report.Rmd`, weather trends, and BCP reports for all user assets. Run by `fpren-comprehensive-2pm.service`. |
| `generate_and_email.R` | Renders a report and emails the PDF via SMTP. |

---

## Common Parameters

Most templates accept these parameters via `rmarkdown::render(params = list(...))`:

```r
rmarkdown::render("reports/business_continuity_report.Rmd",
  params = list(
    username          = "broadcast_admin",
    asset_name        = "WUFT Studio B",
    address           = "1600 SW 23rd Dr, Gainesville FL",
    lat               = 29.6516,
    lon               = -82.3248,
    zip               = "32608",
    city              = "Gainesville",
    nearest_airport_icao = "KGNV",
    nearest_airport_name = "Gainesville Regional Airport",
    asset_type        = "Radio Station",
    mongo_uri         = "mongodb://localhost:27017/",
    days_back         = 7
  ),
  output_file = "/tmp/bcp_report.pdf"
)
```

---

## Output Files (`output/`)

PDF reports are written to `reports/output/`. Files in this directory are ephemeral —
they are not in version control and may be regenerated at any time.

---

## Dependencies

All templates require:
- `rmarkdown`, `knitr`, `ggplot2`, `dplyr`, `mongolite`
- `census_ai_analyzer.py` reachable at `../weather_rss/census_ai_analyzer.py` (for AI narrative sections)
- Active MongoDB connection at `MONGO_URI` (default `mongodb://localhost:27017/`)

---

## Gotchas

- BCP reports call `census_ai_analyzer.py` via `system2()` — AI narrative sections will be blank if `UF_LITELLM_API_KEY` is not set or LiteLLM is unreachable. The rest of the report still renders.
- PDF rendering requires LaTeX (`tinytex` or system `texlive`). If LaTeX is missing, render as HTML instead.
- `generate_comprehensive_2pm.R` generates BCP reports for ALL user assets in MongoDB — if many assets exist this can take several minutes.
- Log files (`*.log`) in this directory are output logs from report generation runs, not code. They are safe to delete.
