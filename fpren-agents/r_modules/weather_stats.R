source(file.path(dirname(sys.frame(1)$ofile %||% "."), "fpren_mongo.R"))
suppressPackageStartupMessages({ library(ggplot2); library(scales); library(tidyr) })

CHART_DIR <- file.path(getwd(), "reports", "charts")
dir.create(CHART_DIR, recursive=TRUE, showWarnings=FALSE)

load_obs <- function(days=14) {
  df <- pull_recent(col_obs, days=days)
  if (nrow(df)==0) return(df)
  df <- parse_ts(df, "observed_time")
  df$temp_f   <- as.numeric(df$temperature)
  df$wind_mph <- as.numeric(df$wind_speed)
  df$wind_dir <- as.numeric(df$wind_direction)
  df$station  <- as.character(df$station_id)
  df$day      <- as.Date(df$observed_time)
  df
}

temp_daily_summary <- function(days=14) {
  df <- load_obs(days)
  if (nrow(df)==0) return(data.frame())
  df %>% dplyr::filter(!is.na(temp_f)) %>%
    dplyr::group_by(station, day) %>%
    dplyr::summarise(temp_min=min(temp_f), temp_mean=mean(temp_f),
                     temp_max=max(temp_f), .groups="drop")
}

wind_rose_data <- function(days=14) {
  df <- load_obs(days) %>% dplyr::filter(!is.na(wind_mph), wind_mph>0)
  df$dir_octant <- cut(df$wind_dir%%360,
    breaks=c(0,45,90,135,180,225,270,315,360),
    labels=c("N","NE","E","SE","S","SW","W","NW"), include.lowest=TRUE)
  df$speed_band <- cut(df$wind_mph, breaks=c(0,5,10,20,Inf),
    labels=c("0-5","6-10","11-20","20+"), include.lowest=TRUE)
  df %>% dplyr::count(dir_octant, speed_band)
}

plot_temp_ribbon <- function(days=14) {
  df <- temp_daily_summary(days); if(nrow(df)==0) return(invisible(NULL))
  p <- ggplot(df, aes(x=day)) +
    geom_ribbon(aes(ymin=temp_min, ymax=temp_max, fill=station), alpha=0.25) +
    geom_line(aes(y=temp_mean, colour=station), linewidth=1) +
    scale_x_date(date_breaks="2 days", date_labels="%b %d") +
    labs(title=sprintf("Temperature range — last %d days",days), x=NULL, y="°F") +
    theme_minimal(base_size=13)
  ggsave(file.path(CHART_DIR,"temp_ribbon.png"), p, width=10, height=4, dpi=150)
  p
}

plot_wind_rose <- function(days=14) {
  df <- wind_rose_data(days); if(nrow(df)==0) return(invisible(NULL))
  p <- ggplot(df, aes(x=dir_octant, y=n, fill=speed_band)) +
    geom_col(width=0.85) + coord_polar(start=-pi/8) +
    scale_fill_manual(values=c("0-5"="#B5D4F4","6-10"="#378ADD","11-20"="#185FA5","20+"="#042C53")) +
    labs(title=sprintf("Wind rose — last %d days",days), x=NULL, y="Obs", fill="mph") +
    theme_minimal(base_size=12) + theme(axis.text.y=element_blank())
  ggsave(file.path(CHART_DIR,"wind_rose.png"), p, width=6, height=6, dpi=150)
  p
}

if (!interactive()) {
  cat("\n=== FPREN Weather Statistics ===\n")
  print(temp_daily_summary(14))
  plot_temp_ribbon(14); plot_wind_rose(14)
  cat("Charts saved to", CHART_DIR, "\n")
}
