#!/usr/bin/env Rscript
# outcome_scorer.R — FPREN Agent Execution Outcome Scorer
#
# Reads unscored records from weather_rss.execution_log, computes
# latency_score, retry_score, quality_score, and composite_score, writes
# the scores back to each record, and upserts per-(agent, model_tier,
# prompt_file) aggregates to weather_rss.routing_scores.
#
# Called hourly by director_with_r.py via RBridge.run_module("outcome_scorer")
# Also called from reports/generate_comprehensive_2pm.R at 2PM daily.
#
# Scoring weights:
#   latency_score  (40%)  1 - min(latency_ms / MAX_LATENCY_MS, 1)
#   retry_score    (30%)  1 - min(retry_count / MAX_RETRY, 1)
#   quality_score  (30%)  1.0 if status=="ok", else 0.0
#   composite             0.40*latency + 0.30*retry + 0.30*quality

suppressPackageStartupMessages({
  library(mongolite)
  library(dplyr)
  library(lubridate)
  library(jsonlite)
})

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0) a else b

# ── Environment / config ─────────────────────────────────────────────────────
.env_file <- file.path(Sys.getenv("HOME"), "Fpren-main",
                        "weather_station", ".env")
if (file.exists(.env_file)) {
  for (line in readLines(.env_file, warn = FALSE)) {
    if (grepl("^[A-Z_]+=", line) && !grepl("^#", line)) {
      parts <- strsplit(line, "=", fixed = TRUE)[[1]]
      if (length(parts) >= 2 && nchar(parts[1]) > 0) {
        do.call(Sys.setenv, setNames(list(paste(parts[-1], collapse = "=")), parts[1]))
      }
    }
  }
}

MONGO_URI      <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME        <- "weather_rss"
MAX_LATENCY_MS <- 30000   # 30 s → latency_score = 0
MAX_RETRY      <- 3       # 3+ retries → retry_score = 0

ts_now <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ")

col_exec   <- mongolite::mongo("execution_log",  db = DB_NAME, url = MONGO_URI)
col_scores <- mongolite::mongo("routing_scores", db = DB_NAME, url = MONGO_URI)

# ── 1. Load unscored records ──────────────────────────────────────────────────
cat("[outcome_scorer]", ts_now(), "loading unscored execution_log records\n")

raw <- tryCatch(
  col_exec$find(
    '{"score":{"$exists":false}}',
    fields = paste0('{"_id":1,"agent_name":1,"model_tier":1,"prompt_file":1,',
                    '"latency_ms":1,"token_count":1,"retry_count":1,',
                    '"status":1,"timestamp":1}')
  ),
  error = function(e) {
    message("[outcome_scorer] DB read error: ", e$message)
    data.frame()
  }
)

if (nrow(raw) == 0) {
  cat("[outcome_scorer] no unscored records — nothing to do\n")
  quit(save = "no", status = 0)
}

cat(sprintf("[outcome_scorer] scoring %d records\n", nrow(raw)))

# ── 2. Score each record ──────────────────────────────────────────────────────
score_record <- function(latency_ms, retry_count, status) {
  lat   <- max(0, as.numeric(latency_ms  %||% MAX_LATENCY_MS))
  ret   <- max(0, as.integer(retry_count %||% 0L))
  stat  <- as.character(status %||% "error")

  ls <- 1 - min(lat / MAX_LATENCY_MS, 1)
  rs <- 1 - min(ret / MAX_RETRY,      1)
  qs <- if (!is.na(stat) && stat == "ok") 1.0 else 0.0

  list(
    latency_score = round(ls,            4),
    retry_score   = round(rs,            4),
    quality_score = round(qs,            4),
    score         = round(0.4*ls + 0.3*rs + 0.3*qs, 4)
  )
}

scored <- raw %>%
  rowwise() %>%
  mutate(
    .s            = list(score_record(latency_ms, retry_count, status)),
    latency_score = .s$latency_score,
    retry_score   = .s$retry_score,
    quality_score = .s$quality_score,
    score         = .s$score,
    scored_at     = ts_now()
  ) %>%
  select(-.s) %>%
  ungroup()

# ── 3. Write scores back to execution_log ────────────────────────────────────
n_written <- 0L
for (i in seq_len(nrow(scored))) {
  row <- scored[i, ]
  oid <- as.character(row[["_id"]])
  tryCatch({
    col_exec$update(
      sprintf('{"_id":{"$oid":"%s"}}', oid),
      sprintf(
        paste0('{"$set":{"score":%f,"latency_score":%f,',
               '"retry_score":%f,"quality_score":%f,"scored_at":"%s"}}'),
        row$score, row$latency_score,
        row$retry_score, row$quality_score, row$scored_at
      )
    )
    n_written <- n_written + 1L
  }, error = function(e) {
    message("[outcome_scorer] update error on ", oid, ": ", e$message)
  })
}
cat(sprintf("[outcome_scorer] wrote scores to %d/%d records\n",
            n_written, nrow(scored)))

# ── 4. Aggregate per (agent_name, model_tier, prompt_file) ───────────────────
agg <- scored %>%
  mutate(
    agent_name  = as.character(agent_name  %||% ""),
    model_tier  = as.character(model_tier  %||% ""),
    prompt_file = as.character(prompt_file %||% ""),
    token_count = as.numeric(token_count   %||% 0),
    retry_count = as.integer(retry_count   %||% 0L)
  ) %>%
  group_by(agent_name, model_tier, prompt_file) %>%
  summarise(
    n           = n(),
    avg_score   = round(mean(score,       na.rm = TRUE), 4),
    avg_latency = round(mean(latency_ms,  na.rm = TRUE), 1),
    avg_tokens  = round(mean(token_count, na.rm = TRUE), 0),
    retry_rate  = round(mean(retry_count > 0, na.rm = TRUE), 4),
    error_rate  = round(mean(status != "ok",  na.rm = TRUE), 4),
    .groups     = "drop"
  ) %>%
  mutate(updated_at = ts_now())

for (i in seq_len(nrow(agg))) {
  row <- agg[i, ]
  key <- toJSON(
    list(
      agent_name  = unbox(row$agent_name),
      model_tier  = unbox(row$model_tier),
      prompt_file = unbox(row$prompt_file)
    ),
    auto_unbox = TRUE
  )
  val <- toJSON(
    list(`$set` = list(
      n           = unbox(as.integer(row$n)),
      avg_score   = unbox(row$avg_score),
      avg_latency = unbox(row$avg_latency),
      avg_tokens  = unbox(as.integer(row$avg_tokens)),
      retry_rate  = unbox(row$retry_rate),
      error_rate  = unbox(row$error_rate),
      updated_at  = unbox(row$updated_at)
    )),
    auto_unbox = TRUE
  )
  tryCatch(
    col_scores$update(key, val, upsert = TRUE),
    error = function(e) {
      message("[outcome_scorer] routing_scores upsert error: ", e$message)
    }
  )
}

cat(sprintf("[outcome_scorer] upserted %d routing_score aggregates\n", nrow(agg)))
cat("[outcome_scorer] done\n")
