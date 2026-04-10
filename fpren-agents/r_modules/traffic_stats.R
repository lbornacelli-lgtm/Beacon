source(file.path(dirname(sys.frame(1)$ofile %||% "."), "fpren_mongo.R"))
suppressPackageStartupMessages({ library(ggplot2); library(scales); library(tidyr) })

CHART_DIR <- file.path(getwd(), "reports", "charts")
dir.create(CHART_DIR, recursive=TRUE, showWarnings=FALSE)

load_traffic <- function(days=30) {
  df <- pull_recent(col_fl511, days=days)
  if (nrow(df)==0) return(df)
  df <- parse_ts(df)
  df$hour <- lubridate::hour(df$inserted_at)
  df$wday <- lubridate::wday(df$inserted_at, label=TRUE, abbr=TRUE)
  df$day  <- as.Date(df$inserted_at)
  df$road <- as.character(df$road)
  df$type <- as.character(df$event_type)
  df
}

top_roads <- function(days=30, n=15) {
  df <- load_traffic(days)
  if (nrow(df)==0) return(data.frame())
  df %>% dplyr::filter(!is.na(road), road!="") %>%
    dplyr::count(road, sort=TRUE) %>% head(n)
}

peak_hours <- function(days=30) {
  df <- load_traffic(days); if(nrow(df)==0) return(data.frame())
  df %>% dplyr::count(hour) %>%
    dplyr::mutate(period=dplyr::case_when(
      hour %in% 6:9   ~ "AM peak",
      hour %in% 16:19 ~ "PM peak",
      TRUE            ~ "Off-peak"))
}

plot_peak_hours <- function(days=30) {
  df <- peak_hours(days); if(nrow(df)==0) return(invisible(NULL))
  p <- ggplot(df, aes(x=hour, y=n, fill=period)) + geom_col(width=0.85) +
    scale_fill_manual(values=c("AM peak"="#E24B4A","PM peak"="#D85A30","Off-peak"="#B5D4F4")) +
    scale_x_continuous(breaks=seq(0,23,3), labels=sprintf("%02d:00",seq(0,23,3))) +
    labs(title=sprintf("Traffic incidents by hour — last %d days",days),
         x="Hour", y="Incidents", fill=NULL) +
    theme_minimal(base_size=13) + theme(legend.position="top")
  ggsave(file.path(CHART_DIR,"traffic_peak_hours.png"), p, width=9, height=4, dpi=150)
  p
}

plot_traffic_heatmap <- function(days=30) {
  df <- load_traffic(days); if(nrow(df)==0) return(invisible(NULL))
  heat <- df %>% dplyr::count(wday, hour)
  p <- ggplot(heat, aes(x=hour, y=wday, fill=n)) +
    geom_tile(colour="white", linewidth=0.4) +
    scale_fill_gradient(low="#FAEEDA", high="#854F0B") +
    labs(title="Traffic heatmap — hour × day", x="Hour", y=NULL, fill="Count") +
    theme_minimal(base_size=12)
  ggsave(file.path(CHART_DIR,"traffic_heatmap.png"), p, width=9, height=4, dpi=150)
  p
}

if (!interactive()) {
  cat("\n=== FPREN Traffic Statistics ===\n")
  print(top_roads(30))
  plot_peak_hours(30); plot_traffic_heatmap(30)
  cat("Charts saved to", CHART_DIR, "\n")
}
