#!/usr/bin/env Rscript
suppressPackageStartupMessages(library(rmarkdown))

REPORT_DIR <- file.path(dirname(getwd()), "reports")
OUTPUT_DIR <- file.path(getwd(), "reports", "output")
dir.create(OUTPUT_DIR, recursive=TRUE, showWarnings=FALSE)

timestamp <- format(Sys.time(), "%Y%m%d_%H%M")

reports <- list(
  list(rmd=file.path(REPORT_DIR,"fpren_alert_report.Rmd"),
       output=file.path(OUTPUT_DIR,paste0("daily_alert_report_",timestamp)),
       params=list(days_back=1, zone_label="All Florida",
                   mongo_uri="mongodb://localhost:27017/")),
  list(rmd=file.path(REPORT_DIR,"weather_trends_report.Rmd"),
       output=file.path(OUTPUT_DIR,paste0("weekly_trends_",timestamp)),
       params=list(icao="KGNV", city_name="Gainesville",
                   mongo_uri="mongodb://localhost:27017/"))
)

render_one <- function(r, format) {
  ext <- if(format=="html_document") ".html" else ".pdf"
  ofile <- paste0(r$output, ext)
  cat(sprintf("[%s] %s → %s\n", format(Sys.time(),"%H:%M:%S"), basename(r$rmd), basename(ofile)))
  tryCatch(
    rmarkdown::render(r$rmd, output_format=format, output_file=ofile,
                      params=r$params, quiet=TRUE, envir=new.env(parent=globalenv())),
    error=function(e) cat("  ERROR:", conditionMessage(e), "\n")
  )
}

cat("=== FPREN Report Renderer ===\n")
for (r in reports) { render_one(r,"html_document"); render_one(r,"pdf_document") }

manifest <- data.frame(
  report=basename(sapply(reports,`[[`,"rmd")), timestamp=timestamp,
  html=paste0(sapply(reports,function(r) basename(r$output)),".html"),
  pdf=paste0(sapply(reports,function(r) basename(r$output)),".pdf"),
  stringsAsFactors=FALSE)
jsonlite::write_json(manifest, file.path(OUTPUT_DIR,"manifest.json"), pretty=TRUE, auto_unbox=TRUE)
cat("Done. Outputs in:", OUTPUT_DIR, "\n")
