#!/usr/bin/env Rscript
# FPREN Comprehensive 2PM Report Generator
#
# Runs daily at 2:00 PM ET via fpren-comprehensive-2pm.timer
# Generates:
#   1. Daily alert summary report (all Florida, 7 days)
#   2. Weather trends PDF for all 16 FL cities (last 7 days)
#   3. Business Continuity Plan for every user asset in MongoDB
#
# At the end, emails a summary to the configured SMTP recipient.
#
# Usage:
#   Rscript generate_comprehensive_2pm.R

suppressPackageStartupMessages({
  library(rmarkdown)
  library(mongolite)
  library(jsonlite)
  library(emayili)
  library(lubridate)
  library(withr)
  library(callr)
})

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && !is.na(a[1]) &&
                              nchar(as.character(a[1])) > 0) a else b

ts      <- function() format(Sys.time(), "[%Y-%m-%d %H:%M:%S]")
log_msg <- function(...) cat(ts(), ..., "\n", sep = " ")

MONGO_URI    <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")
REPORT_DIR   <- "/home/ufuser/Fpren-main/reports/output"
REPORTS_BASE <- "/home/ufuser/Fpren-main/reports"
SMTP_CFG     <- "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
MAX_WORKERS  <- 2L   # parallel render slots (leave 1 core free)

dir.create(REPORT_DIR, showWarnings = FALSE, recursive = TRUE)

smtp_cfg  <- tryCatch(fromJSON(SMTP_CFG), error = function(e) list())
smtp_host <- smtp_cfg$smtp_host %||% "smtp.ufl.edu"
smtp_port <- as.integer(smtp_cfg$smtp_port %||% 25)
mail_from <- smtp_cfg$mail_from %||% "lawrence.bornace@ufl.edu"
mail_to   <- smtp_cfg$mail_to   %||% "lawrence.bornace@ufl.edu"

generated <- list()
failed    <- list()

WX_CITIES <- data.frame(
  icao = c("KJAX","KTLH","KGNV","KOCF","KMCO","KDAB",
           "KTPA","KSPG","KSRQ","KRSW","KMIA","KFLL",
           "KPBI","KEYW","KPNS","KECP"),
  city = c("Jacksonville","Tallahassee","Gainesville","Ocala","Orlando","Daytona Beach",
           "Tampa","St. Petersburg","Sarasota","Fort Myers","Miami","Fort Lauderdale",
           "West Palm Beach","Key West","Pensacola","Panama City"),
  stringsAsFactors = FALSE
)

# FL county centroids for nearest-county lookup in BCP reports
FL_COUNTY_CENTROIDS <- data.frame(
  county = c(
    "Alachua","Baker","Bay","Bradford","Brevard","Broward",
    "Calhoun","Charlotte","Citrus","Clay","Collier","Columbia",
    "Miami-Dade","DeSoto","Dixie","Duval","Escambia","Flagler",
    "Franklin","Gadsden","Gilchrist","Glades","Gulf","Hamilton",
    "Hardee","Hendry","Hernando","Highlands","Hillsborough","Holmes",
    "Indian River","Jackson","Jefferson","Lafayette","Lake","Lee",
    "Leon","Levy","Liberty","Madison","Manatee","Marion",
    "Martin","Monroe","Nassau","Okaloosa","Okeechobee","Orange",
    "Osceola","Palm Beach","Pasco","Pinellas","Polk","Putnam",
    "Saint Johns","Saint Lucie","Santa Rosa","Sarasota","Seminole",
    "Sumter","Suwannee","Taylor","Union","Volusia","Wakulla",
    "Walton","Washington"
  ),
  lat = c(
    29.67,30.33,30.21,29.94,28.23,26.15,
    30.41,26.97,28.84,29.98,26.11,30.22,
    25.55,27.19,29.68,30.33,30.55,29.47,
    29.80,30.59,29.72,26.94,29.87,30.48,
    27.49,26.50,28.55,27.35,27.90,30.87,
    27.74,30.83,30.52,30.07,28.76,26.56,
    30.46,29.31,30.25,30.46,27.48,29.23,
    27.09,24.70,30.52,30.74,27.24,28.45,
    28.06,26.65,28.28,27.86,27.90,29.56,
    29.96,27.35,30.76,27.22,28.66,
    28.69,30.18,30.04,29.98,29.07,30.26,
    30.62,30.63
  ),
  lon = c(
    -82.49,-82.27,-85.62,-82.14,-80.72,-80.46,
    -85.16,-81.94,-82.47,-81.76,-81.39,-82.62,
    -80.60,-81.83,-83.18,-81.66,-87.35,-81.21,
    -84.89,-84.63,-82.74,-81.11,-85.23,-82.97,
    -81.83,-80.91,-82.41,-81.28,-82.33,-85.81,
    -80.57,-85.22,-83.81,-83.16,-81.76,-81.71,
    -84.28,-82.83,-84.88,-83.42,-82.57,-82.07,
    -80.41,-81.52,-81.60,-86.49,-80.88,-81.32,
    -81.26,-80.25,-82.40,-82.77,-81.70,-81.68,
    -81.41,-80.40,-86.92,-82.48,-81.26,
    -82.07,-83.14,-83.61,-82.39,-81.24,-84.39,
    -86.17,-85.67
  ),
  stringsAsFactors = FALSE
)

nearest_county <- function(lat, lon) {
  R    <- 6371
  dlat <- (FL_COUNTY_CENTROIDS$lat - lat) * pi / 180
  dlon <- (FL_COUNTY_CENTROIDS$lon - lon) * pi / 180
  a    <- sin(dlat/2)^2 +
          cos(lat*pi/180) * cos(FL_COUNTY_CENTROIDS$lat*pi/180) * sin(dlon/2)^2
  dist <- R * 2 * atan2(sqrt(a), sqrt(1 - a))
  FL_COUNTY_CENTROIDS$county[which.min(dist)]
}

# ── Parallel render helper ────────────────────────────────────────────────────
# Each job is a list: rmd, output_file, params_list, label
run_batch_renders <- function(jobs, max_workers = MAX_WORKERS) {
  gen  <- list()
  fail <- character(0)
  i    <- 1L
  n    <- length(jobs)

  while (i <= n) {
    batch_end <- min(i + max_workers - 1L, n)
    batch     <- jobs[i:batch_end]

    # Launch background R processes
    procs <- lapply(batch, function(job) {
      log_msg("Launching:", job$label)
      callr::r_bg(
        func = function(rmd, output_file, params_list) {
          suppressPackageStartupMessages({ library(rmarkdown); library(withr) })
          withr::with_dir(tempdir(), rmarkdown::render(
            input             = rmd,
            output_file       = output_file,
            intermediates_dir = tempdir(),
            params            = params_list,
            quiet             = TRUE
          ))
          file.exists(output_file)
        },
        args      = list(rmd         = job$rmd,
                         output_file = job$output_file,
                         params_list = job$params_list),
        libpath   = .libPaths(),
        supervise = TRUE
      )
    })

    # Wait for all processes in batch and collect results
    for (j in seq_along(procs)) {
      proc <- procs[[j]]
      job  <- batch[[j]]
      ok   <- tryCatch({
        proc$wait(timeout = 600000L)   # 10-minute hard timeout per job
        proc$get_result()
      }, error = function(e) {
        log_msg("ERROR:", job$label, "--", conditionMessage(e))
        FALSE
      })
      if (isTRUE(ok) && file.exists(job$output_file)) {
        log_msg("OK:", job$label)
        gen <- c(gen, list(list(label = job$label, file = basename(job$output_file))))
      } else {
        log_msg("FAIL:", job$label)
        fail <- c(fail, job$label)
      }
    }

    i <- batch_end + 1L
  }

  list(generated = gen, failed = fail)
}

stamp <- format(Sys.time(), "%Y%m%d_%H%M")

# ── 0. Agent Execution Scoring ───────────────────────────────────────────────
log_msg("=== 0. Outcome Scorer ===")
outcome_scorer_script <- "/home/ufuser/Fpren-main/fpren-agents/r_modules/outcome_scorer.R"
if (file.exists(outcome_scorer_script)) {
  tryCatch({
    result <- system2("Rscript", args = c("--vanilla", outcome_scorer_script),
                      env = paste0("MONGO_URI=", MONGO_URI),
                      stdout = TRUE, stderr = TRUE)
    log_msg("outcome_scorer.R output:", paste(result, collapse = " | "))
  }, error = function(e) log_msg("ERROR running outcome_scorer.R:", conditionMessage(e)))
} else {
  log_msg("WARN: outcome_scorer.R not found at", outcome_scorer_script)
}

# ── 1. Alert Summary ─────────────────────────────────────────────────────────
log_msg("=== 1. Alert Summary Report ===")
alert_out <- file.path(REPORT_DIR, paste0("fpren_alert_report_2pm_", stamp, ".pdf"))
alert_res <- run_batch_renders(list(list(
  rmd         = file.path(REPORTS_BASE, "fpren_alert_report.Rmd"),
  output_file = alert_out,
  params_list = list(days_back=7, zone_label="All Florida", mongo_uri=MONGO_URI,
                     severity_filter="all", event_filter="all",
                     date_from="", date_to=""),
  label       = "Alert Summary 7d"
)))
generated <- c(generated, alert_res$generated)
failed    <- c(failed,    as.list(alert_res$failed))

# ── 2. Weather Trends — 16 cities (parallel) ─────────────────────────────────
log_msg("=== 2. Weather Trends (", nrow(WX_CITIES), "cities) ===")
wx_rmd   <- file.path(REPORTS_BASE, "weather_trends_report.Rmd")
start_d  <- as.character(Sys.Date() - 7)
end_d    <- as.character(Sys.Date())

wx_jobs <- lapply(seq_len(nrow(WX_CITIES)), function(i) {
  icao <- WX_CITIES$icao[i]; city <- WX_CITIES$city[i]
  safe <- gsub("[^A-Za-z0-9]", "_", city)
  list(
    rmd         = wx_rmd,
    output_file = file.path(REPORT_DIR, paste0("weather_trends_", safe, "_2pm_", stamp, ".pdf")),
    params_list = list(icao=icao, city_name=city, start_date=start_d,
                       end_date=end_d, mongo_uri=MONGO_URI),
    label       = paste0("WX: ", city)
  )
})

wx_res    <- run_batch_renders(wx_jobs)
generated <- c(generated, wx_res$generated)
failed    <- c(failed, as.list(wx_res$failed))

# ── 3. Business Continuity Plans (parallel) ───────────────────────────────────
log_msg("=== 3. Business Continuity Plans ===")
bcp_rmd   <- file.path(REPORTS_BASE, "business_continuity_report.Rmd")
users_col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)

if (is.null(users_col)) {
  log_msg("ERROR: Cannot connect to MongoDB for user assets")
  failed <- c(failed, list("BCP: MongoDB unavailable"))
} else {
  users_with_assets <- tryCatch({
    r <- users_col$find('{"assets":{"$exists":true,"$not":{"$size":0}}}',
                        fields='{"username":1,"assets":1,"_id":0}')
    users_col$disconnect()
    r
  }, error = function(e) {
    tryCatch(users_col$disconnect(), error=function(e2) NULL)
    log_msg("ERROR querying assets:", conditionMessage(e))
    data.frame()
  })

  if (nrow(users_with_assets) == 0) {
    log_msg("No users with assets — skipping BCP")
  } else {
    log_msg("Found", nrow(users_with_assets), "users with assets")

    gf <- function(asset, field) {
      v <- tryCatch(as.character(asset[[field]]), error=function(e) "")
      if (is.null(v) || length(v)==0 || is.na(v[1])) "" else v[1]
    }

    bcp_jobs <- list()
    for (u_idx in seq_len(nrow(users_with_assets))) {
      uname  <- users_with_assets$username[u_idx]
      assets <- users_with_assets$assets[[u_idx]]
      if (is.null(assets) || length(assets) == 0) next

      is_df    <- is.data.frame(assets)
      n_assets <- if (is_df) nrow(assets) else length(assets)

      for (a_idx in seq_len(n_assets)) {
        asset  <- if (is_df) assets[a_idx, ] else assets[[a_idx]]
        aname  <- gf(asset, "asset_name") %||% paste0("Asset_", a_idx)
        safe_a <- gsub("[^A-Za-z0-9]", "_", aname)
        out    <- file.path(REPORT_DIR,
                    paste0("bcp_", uname, "_", safe_a, "_", stamp, ".pdf"))

        lat <- tryCatch(as.numeric(gf(asset,"lat")), error=function(e) 29.65)
        lon <- tryCatch(as.numeric(gf(asset,"lon")), error=function(e) -82.33)
        if (is.na(lat)) lat <- 29.65
        if (is.na(lon)) lon <- -82.33

        county <- tryCatch(nearest_county(lat, lon), error=function(e) "Alachua")

        bcp_jobs <- c(bcp_jobs, list(list(
          rmd         = bcp_rmd,
          output_file = out,
          params_list = list(
            username             = uname,
            asset_name           = aname,
            address              = gf(asset,"address"),
            lat                  = lat,
            lon                  = lon,
            zip                  = gf(asset,"zip"),
            city                 = gf(asset,"city") %||% "Unknown",
            county               = county,
            nearest_airport_icao = gf(asset,"nearest_airport_icao") %||% "KGNV",
            nearest_airport_name = gf(asset,"nearest_airport_name") %||% "Gainesville Regional",
            asset_type           = gf(asset,"asset_type") %||% "Facility",
            notes                = gf(asset,"notes"),
            mongo_uri            = MONGO_URI,
            days_back            = 30
          ),
          label = paste0("BCP - ", uname, "/", aname)
        )))
      }
    }

    if (length(bcp_jobs) > 0) {
      bcp_res   <- run_batch_renders(bcp_jobs)
      generated <- c(generated, bcp_res$generated)
      failed    <- c(failed, as.list(bcp_res$failed))
    }
  }
}

# ── 4. Summary email ─────────────────────────────────────────────────────────
log_msg("=== 4. Summary email ===")
n_ok   <- length(generated)
n_fail <- length(failed)
gen_lines  <- if (n_ok>0)   paste0(sapply(generated, function(x) paste0("  OK   ",x$label," -> ",x$file)), collapse="\n") else "  (none)"
fail_lines <- if (n_fail>0) paste0(sapply(failed,    function(x) paste0("  FAIL ",x)),                    collapse="\n") else "  (none)"

tryCatch({
  em <- envelope() %>%
    from(mail_from) %>% to(mail_to) %>%
    subject(paste0("FPREN 2PM Comprehensive Report -- ",
                   format(Sys.Date(),"%Y-%m-%d")," (",n_ok," OK, ",n_fail," failed)")) %>%
    text(paste0(
      "FPREN Daily 2PM Comprehensive Report\n=====================================\n\n",
      "Generated: ", format(Sys.time(),"%Y-%m-%d %H:%M ET"), "\n\n",
      "Reports generated (", n_ok, "):\n", gen_lines, "\n\n",
      "Failures (", n_fail, "):\n", fail_lines, "\n\n",
      "Output directory: ", REPORT_DIR, "\n\n",
      "-- FPREN Automated Reporting System\n   Florida Public Radio Emergency Network\n"
    ))
  server(host=smtp_host, port=smtp_port, reuse=FALSE)(em, verbose=FALSE)
  log_msg("Summary email sent to", mail_to)
}, error=function(e) log_msg("ERROR sending email:", conditionMessage(e)))

log_msg("=== Done ===", n_ok, "reports generated,", n_fail, "failed.")
cat("COMPREHENSIVE_REPORT_COMPLETE\n")
