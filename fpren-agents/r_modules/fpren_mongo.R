# fpren_mongo.R — Shared MongoDB bridge for all FPREN R modules.
# Source this at the top of every R script: source("fpren_mongo.R")

suppressPackageStartupMessages({
  library(mongolite)
  library(jsonlite)
  library(lubridate)
  library(dplyr)
})

.env_file <- file.path(Sys.getenv("HOME"), "Fpren-main", "weather_station", ".env")
if (file.exists(.env_file)) {
  lines <- readLines(.env_file, warn = FALSE)
  for (line in lines) {
    if (grepl("^[A-Z_]+=", line) && !grepl("^#", line)) {
      parts <- strsplit(line, "=", fixed = TRUE)[[1]]
      key <- parts[1]; val <- paste(parts[-1], collapse = "=")
      Sys.setenv(setNames(list(val), key))
    }
  }
}

MONGO_URI <- Sys.getenv("MONGO_URI", unset = "mongodb://localhost:27017")
DB_NAME   <- "weather_rss"

fpren_col    <- function(col) mongolite::mongo(collection=col, db=DB_NAME, url=MONGO_URI)
col_weather  <- function() fpren_col("weather_processed")
col_traffic  <- function() fpren_col("traffic_processed")
col_alerts   <- function() fpren_col("alerts_processed")
col_tts      <- function() fpren_col("tts_completed")
col_obs      <- function() fpren_col("observations")
col_raw_wx   <- function() fpren_col("weather_alerts")
col_fl511    <- function() fpren_col("fl511_traffic")

pull_recent <- function(col_fn, days=7, extra_query="{}") {
  cutoff <- format(Sys.time() - days*86400, "%Y-%m-%dT%H:%M:%SZ")
  query  <- sprintf('{"$and":[%s,{"inserted_at":{"$gte":{"$date":"%s"}}}]}', extra_query, cutoff)
  tryCatch(col_fn()$find(query), error=function(e){ message("pull_recent: ",e$message); data.frame() })
}

parse_ts <- function(df, col="inserted_at") {
  if (col %in% names(df)) df[[col]] <- lubridate::ymd_hms(df[[col]], quiet=TRUE)
  df
}
