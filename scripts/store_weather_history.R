#!/usr/bin/env Rscript
# store_weather_history.R
# Reads current airport_metar data for the 16 FL FPREN cities,
# computes derived fields, and upserts hourly snapshots into
# weather_history collection in the weather_rss MongoDB database.
# Run hourly via fpren-weather-history.timer.
#
# Compression rule: skip write if temp change < 2°F, wind < 5 kt,
# visibility change < 1 mi (compared to the most recent stored record
# for that station) — unless it has been more than 2 hours since last write.

suppressPackageStartupMessages({
  library(mongolite)
  library(dplyr)
})

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && !is.na(a[1])) a[1] else b

MONGO_URI  <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")
PURGE_DAYS <- 90L                        # keep last 90 days of records
NOW        <- Sys.time()

WX_CITIES <- data.frame(
  icao = c("KJAX","KTLH","KGNV","KOCF","KMCO","KDAB",
           "KTPA","KSPG","KSRQ","KRSW","KMIA","KFLL",
           "KPBI","KEYW","KPNS","KECP"),
  city = c("Jacksonville","Tallahassee","Gainesville","Ocala","Orlando","Daytona Beach",
           "Tampa","St. Petersburg","Sarasota","Fort Myers","Miami","Fort Lauderdale",
           "West Palm Beach","Key West","Pensacola","Panama City"),
  stringsAsFactors = FALSE
)

# ── Helpers ───────────────────────────────────────────────────────────────────

get_col <- function(collection) {
  tryCatch(
    mongo(collection = collection, db = "weather_rss", url = MONGO_URI),
    error = function(e) { message("MongoDB connect error: ", conditionMessage(e)); NULL }
  )
}

feels_like_f <- function(temp_c, dewp_c, wspd_kt) {
  if (is.na(temp_c)) return(NA_real_)
  temp_f <- temp_c * 9/5 + 32
  if (!is.na(wspd_kt) && temp_f <= 50 && wspd_kt > 3) {
    wspd_mph <- wspd_kt * 1.15078
    wc <- 35.74 + 0.6215*temp_f - 35.75*(wspd_mph^0.16) + 0.4275*temp_f*(wspd_mph^0.16)
    return(round(wc, 1))
  }
  if (!is.na(dewp_c) && temp_f >= 80) {
    rh <- 100 * exp((17.625 * dewp_c) / (243.04 + dewp_c)) /
               exp((17.625 * temp_c) / (243.04 + temp_c))
    hi <- -42.379 + 2.04901523*temp_f + 10.14333127*rh -
          0.22475541*temp_f*rh - 0.00683783*temp_f^2 -
          0.05481717*rh^2 + 0.00122874*temp_f^2*rh +
          0.00085282*temp_f*rh^2 - 0.00000199*temp_f^2*rh^2
    return(round(hi, 1))
  }
  round(temp_f, 1)
}

rh_from_dewpoint <- function(temp_c, dewp_c) {
  if (is.na(temp_c) || is.na(dewp_c)) return(NA_real_)
  round(100 * exp((17.625 * dewp_c) / (243.04 + dewp_c)) /
              exp((17.625 * temp_c) / (243.04 + temp_c)), 1)
}

# ── Fetch current METAR data for the 16 cities ───────────────────────────────

icao_list <- paste0('["', paste(WX_CITIES$icao, collapse='","'), '"]')
query     <- paste0('{"icaoId":{"$in":', icao_list, '}}')

metar_col <- get_col("airport_metar")
if (is.null(metar_col)) {
  message("FATAL: cannot connect to airport_metar — aborting")
  quit(status = 1)
}

metar_df <- tryCatch({
  df <- metar_col$find(query,
    fields = '{"icaoId":1,"temp":1,"dewp":1,"wspd":1,"wdir":1,
               "visib":1,"fltCat":1,"obsTime":1,"wxString":1,"rhum":1,"_id":0}')
  metar_col$disconnect()
  df
}, error = function(e) {
  tryCatch(metar_col$disconnect(), error=function(e2) NULL)
  message("METAR fetch error: ", conditionMessage(e))
  NULL
})

if (is.null(metar_df) || nrow(metar_df) == 0) {
  message("No METAR data returned — skipping this run")
  quit(status = 0)
}

# ── Open / create weather_history collection ──────────────────────────────────

hist_col <- get_col("weather_history")
if (is.null(hist_col)) {
  message("FATAL: cannot connect to weather_history — aborting")
  quit(status = 1)
}

# ── Ensure index for efficient queries ────────────────────────────────────────
tryCatch(
  invisible(capture.output(hist_col$index(add = '{"icao":1,"timestamp":-1}'))),
  error = function(e) NULL   # index may already exist
)
# Note: 90-day TTL expiry on the timestamp field is managed at the MongoDB level.
# To create manually: db.weather_history.createIndex({timestamp:1},{expireAfterSeconds:7776000})
# TTL was applied via mongosh on 2026-04-11 — do not re-create here (would conflict).

# ── Process each station ──────────────────────────────────────────────────────

TEMP_THRESH <- 2.0   # °F change threshold
WIND_THRESH <- 5.0   # kt change threshold
VIS_THRESH  <- 1.0   # mi change threshold
MAX_GAP_H   <- 2.0   # always write if last record is older than this many hours

written <- 0L
skipped <- 0L

for (i in seq_len(nrow(metar_df))) {
  row  <- metar_df[i, ]
  icao <- row$icaoId

  city_name <- WX_CITIES$city[WX_CITIES$icao == icao]
  if (length(city_name) == 0) city_name <- icao

  temp_f    <- if (!is.na(row$temp)) round(row$temp * 9/5 + 32, 1) else NA_real_
  fl_f      <- feels_like_f(row$temp,
                             if ("dewp"  %in% names(row)) row$dewp  else NA,
                             if ("wspd"  %in% names(row)) row$wspd  else NA)
  hum       <- if ("rhum" %in% names(row) && !is.na(row$rhum)) round(row$rhum, 1)
               else rh_from_dewpoint(row$temp,
                      if ("dewp" %in% names(row)) row$dewp else NA)
  wind_kt   <- suppressWarnings(if ("wspd"  %in% names(row)) as.numeric(row$wspd)  else NA_real_)
  wind_dir  <- suppressWarnings(if ("wdir"  %in% names(row)) as.numeric(row$wdir)  else NA_real_)
  vis_mi    <- suppressWarnings(if ("visib" %in% names(row)) as.numeric(row$visib) else NA_real_)
  flt_cat   <- if ("fltCat"   %in% names(row) && !is.na(row$fltCat))
                 as.character(row$fltCat) else "UNK"
  wx_desc   <- if ("wxString" %in% names(row) && !is.na(row$wxString) && nchar(row$wxString) > 0)
                 as.character(row$wxString) else ""

  # Pull most-recent stored record for change comparison
  last_rec <- tryCatch(
    hist_col$find(
      sprintf('{"icao":"%s"}', icao),
      fields = '{"temp_f":1,"wind_speed":1,"visibility":1,"timestamp":1,"_id":0}',
      sort   = '{"timestamp":-1}',
      limit  = 1
    ),
    error = function(e) data.frame()
  )

  # Decide whether to write
  do_write <- TRUE
  if (nrow(last_rec) > 0) {
    age_h <- as.numeric(difftime(NOW, last_rec$timestamp[1], units = "hours"))
    if (!is.na(age_h) && age_h < MAX_GAP_H) {
      dt <- abs(temp_f    - (last_rec$temp_f[1]    %||% Inf))
      dw <- abs(wind_kt   - (last_rec$wind_speed[1] %||% Inf))
      dv <- abs(vis_mi    - (last_rec$visibility[1] %||% Inf))
      if (all(c(dt, dw, dv) < c(TEMP_THRESH, WIND_THRESH, VIS_THRESH), na.rm = TRUE)) {
        do_write <- FALSE
      }
    }
  }

  if (!do_write) {
    skipped <- skipped + 1L
    next
  }

  doc <- data.frame(
    icao        = icao,
    city        = city_name,
    timestamp   = format(NOW, "%Y-%m-%dT%H:%M:%SZ"),
    temp_f      = if (!is.na(temp_f))   temp_f   else NA_real_,
    feels_like_f = if (!is.na(fl_f))    fl_f     else NA_real_,
    humidity    = if (!is.na(hum))      hum      else NA_real_,
    wind_speed  = if (!is.na(wind_kt))  wind_kt  else NA_real_,
    wind_dir    = if (!is.na(wind_dir)) wind_dir else NA_real_,
    visibility  = if (!is.na(vis_mi))   vis_mi   else NA_real_,
    flight_cat  = flt_cat,
    wx_desc     = wx_desc,
    stringsAsFactors = FALSE
  )

  tryCatch({
    hist_col$insert(doc)
    written <- written + 1L
  }, error = function(e) {
    message(sprintf("Insert error for %s: %s", icao, conditionMessage(e)))
  })
}

# ── Purge records older than PURGE_DAYS ───────────────────────────────────────

cutoff <- format(NOW - as.difftime(PURGE_DAYS, units = "days"),
                 "%Y-%m-%dT%H:%M:%SZ")
purge_query <- sprintf('{"timestamp":{"$lt":"%s"}}', cutoff)
n_purged <- tryCatch({
  hist_col$remove(purge_query)
  0L   # mongolite remove() return value varies by version; count is non-critical
}, error = function(e) {
  message("Purge error: ", conditionMessage(e))
  0L
})

hist_col$disconnect()

message(sprintf(
  "[%s] weather_history: wrote %d, skipped %d (no change), purged %d old records",
  format(NOW, "%Y-%m-%d %H:%M:%S"), written, skipped, n_purged
))
