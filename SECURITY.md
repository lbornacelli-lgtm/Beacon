# Security Policy

## Scope

FPREN is a research and development platform operated at the University of Florida. It processes public NWS/IPAWS weather data and streams audio over Icecast. The following components are in scope for security review:

- Flask admin dashboard (`weather_rss/web/app.py`) — port 5000
- Shiny monitoring dashboard — port 3838
- MongoDB instance — internal only (port 27017, not exposed externally)
- Icecast streaming server — port 8000

## Sensitive Data

- `weather_station/config/.env` — API keys (UF LiteLLM, ElevenLabs). Not committed to git.
- `weather_rss/config/smtp_config.json` — SMTP credentials. Not committed to git.
- MongoDB `users` collection — dashboard user accounts with bcrypt-hashed passwords.

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it by opening a GitHub issue at:

`https://github.com/lbornacelli-lgtm/FPREN/issues`

For sensitive disclosures, contact the repository maintainer directly via GitHub.

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested remediation (if known)

You can expect an acknowledgment within 5 business days.

## Known Security Notes

- The Flask admin dashboard does not use HTTPS — it is intended for internal/VPN access only.
- Port 5000 is not firewalled at the UF VM level — access should be restricted via UF network controls.
- Icecast admin credentials are separate from Flask admin credentials. The Icecast admin interface is on port 8000.
- MongoDB is bound to localhost only and is not exposed externally.
