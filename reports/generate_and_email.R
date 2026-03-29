#!/usr/bin/env Rscript
# FPREN Alert Report — render PDF and email to recipient
#
# Usage:
#   Rscript generate_and_email.R [days_back] [zone_label]
#
# Examples:
#   Rscript generate_and_email.R          # 7-day All Florida report
#   Rscript generate_and_email.R 30       # 30-day report
#   Rscript generate_and_email.R 1 "Daily Summary"
#
# Environment / config:
#   SMTP config is read from /home/ufuser/Fpren-main/weather_rss/config/smtp_config.json
#   MONGO_URI defaults to mongodb://localhost:27017/

suppressPackageStartupMessages({
  library(rmarkdown)
  library(emayili)
})

# ── Config ────────────────────────────────────────────────────────────────────
args       <- commandArgs(trailingOnly = TRUE)
days_back  <- as.integer(ifelse(length(args) >= 1 && !is.na(suppressWarnings(as.integer(args[1]))),
                                 args[1], 7))
zone_label <- ifelse(length(args) >= 2, args[2], "All Florida")
mongo_uri  <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")

smtp_cfg_path <- "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
smtp_cfg <- tryCatch(
  jsonlite::fromJSON(smtp_cfg_path),
  error = function(e) list()
)

smtp_host <- smtp_cfg$smtp_host %||% "smtp.ufl.edu"
smtp_port <- as.integer(smtp_cfg$smtp_port %||% 25)
mail_from <- smtp_cfg$mail_from %||% "lawrence.bornace@ufl.edu"
mail_to   <- smtp_cfg$mail_to   %||% "lawrence.bornace@ufl.edu"

`%||%` <- function(a, b) if (!is.null(a) && nchar(a) > 0) a else b

# ── Output paths ──────────────────────────────────────────────────────────────
report_dir <- "/home/ufuser/Fpren-main/reports/output"
dir.create(report_dir, showWarnings = FALSE, recursive = TRUE)

timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
output_file <- file.path(report_dir,
                          paste0("fpren_alert_report_", timestamp, ".pdf"))
rmd_path    <- "/home/ufuser/Fpren-main/reports/fpren_alert_report.Rmd"

# ── Render PDF ────────────────────────────────────────────────────────────────
cat(sprintf("[FPREN Report] Rendering %d-day report (%s)...\n", days_back, zone_label))

tryCatch({
  rmarkdown::render(
    input       = rmd_path,
    output_file = output_file,
    params      = list(
      days_back  = days_back,
      zone_label = zone_label,
      mongo_uri  = mongo_uri
    ),
    quiet = TRUE
  )
  cat(sprintf("[FPREN Report] PDF saved: %s\n", output_file))
}, error = function(e) {
  cat(sprintf("[FPREN Report] ERROR rendering PDF: %s\n", conditionMessage(e)))
  quit(status = 1)
})

if (!file.exists(output_file)) {
  cat("[FPREN Report] ERROR: PDF not found after render.\n")
  quit(status = 1)
}

# ── Email PDF ─────────────────────────────────────────────────────────────────
period_label <- paste0(
  format(Sys.time() - lubridate::days(days_back), "%b %d"),
  " – ",
  format(Sys.time(), "%b %d, %Y")
)
subject <- sprintf("FPREN Alert Report — %s (%s)", zone_label, period_label)

cat(sprintf("[FPREN Report] Emailing to %s via %s:%d...\n",
            mail_to, smtp_host, smtp_port))

tryCatch({
  email <- envelope() %>%
    from(mail_from) %>%
    to(mail_to) %>%
    subject(subject) %>%
    text(paste0(
      "FPREN Weather Alert Report\n",
      "==========================\n\n",
      "Period:    ", period_label, "\n",
      "Zone:      ", zone_label, "\n",
      "Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC"), "\n\n",
      "Please find the full PDF report attached.\n\n",
      "-- FPREN Automated Reporting System\n",
      "   Florida Public Radio Emergency Network\n"
    )) %>%
    attachment(output_file)

  smtp <- server(
    host     = smtp_host,
    port     = smtp_port,
    reuse    = FALSE
  )
  smtp(email, verbose = FALSE)
  cat(sprintf("[FPREN Report] Email sent to %s\n", mail_to))
}, error = function(e) {
  cat(sprintf("[FPREN Report] ERROR sending email: %s\n", conditionMessage(e)))
  cat("[FPREN Report] PDF is still saved at:", output_file, "\n")
  quit(status = 2)
})

cat("[FPREN Report] Done.\n")
