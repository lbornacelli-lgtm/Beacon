#!/usr/bin/env Rscript
suppressPackageStartupMessages(library(rmarkdown))

REPORT_DIR <- file.path(getwd(), "reports")
OUTPUT_DIR <- file.path(REPORT_DIR, "output")
dir.create(OUTPUT_DIR, recursive=TRUE, showWarnings=FALSE)

timestamp <- format(Sys.time(), "%Y%m%d_%H%M")

reports <- list(
  list(rmd=file.path(REPORT_DIR,"daily_summary.Rmd"),
       output=file.path(OUTPUT_DIR,paste0("daily_summary_",timestamp)),
       params=list(days=1, lookback_days=30)),
  list(rmd=file.path(REPORT_DIR,"weekly_trends.Rmd"),
       output=file.path(OUTPUT_DIR,paste0("weekly_trends_",timestamp)),
       params=list(weeks=4))
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
