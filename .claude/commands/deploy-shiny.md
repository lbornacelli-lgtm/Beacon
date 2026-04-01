---
description: Deploy shiny_dashboard/app.R to Shiny Server and restart
allowed-tools: ["Bash"]
---

Deploy the Shiny dashboard from the repo to the live Shiny Server path, then restart the service.

Run the following commands in sequence and report the result of each step:

```bash
sudo cp /home/ufuser/Fpren-main/shiny_dashboard/app.R /srv/shiny-server/fpren/app.R
sudo chown shiny:shiny /srv/shiny-server/fpren/app.R
sudo systemctl restart shiny-server
sleep 3
sudo systemctl is-active shiny-server
```

If `shiny-server` is active after restart, report success and the dashboard URL: `http://128.227.67.234:3838`

If it fails, show the last 20 lines of the journal:
```bash
sudo journalctl -u shiny-server -n 20 --no-pager
```
