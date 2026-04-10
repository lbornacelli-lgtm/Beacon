source(file.path(dirname(sys.frame(1)$ofile %||% "."), "fpren_mongo.R"))
suppressPackageStartupMessages({ library(ggplot2); library(tidyr); library(scales); library(zoo) })

CHART_DIR <- file.path(getwd(), "reports", "charts")
dir.create(CHART_DIR, recursive=TRUE, showWarnings=FALSE)

load_alerts <- function(days=30) {
  df <- pull_recent(col_alerts, days=days)
  if (nrow(df)==0) return(df)
  df <- parse_ts(df)
  df$day  <- as.Date(df$inserted_at)
  df$hour <- lubridate::hour(df$inserted_at)
  df$wday <- lubridate::wday(df$inserted_at, label=TRUE)
  df
}

alert_summary_table <- function(days=30) {
  df <- load_alerts(days)
  if (nrow(df)==0) return(data.frame(message="No alert data found"))
  df %>% dplyr::group_by(source, severity) %>%
    dplyr::summarise(count=dplyr::n(), pct=round(dplyr::n()/nrow(df)*100,1), .groups="drop") %>%
    dplyr::arrange(dplyr::desc(count))
}

alert_daily_trend <- function(days=30) {
  df <- load_alerts(days)
  if (nrow(df)==0) return(data.frame())
  df %>% dplyr::count(day, severity) %>%
    tidyr::complete(day, severity, fill=list(n=0)) %>%
    dplyr::group_by(severity) %>% dplyr::arrange(day) %>%
    dplyr::mutate(roll7=zoo::rollmean(n,7,fill=NA,align="right")) %>%
    dplyr::ungroup()
}

plot_alert_severity <- function(days=30) {
  df <- load_alerts(days); if(nrow(df)==0) return(invisible(NULL))
  p <- ggplot(df, aes(x=severity, fill=severity)) +
    geom_bar(width=0.6) +
    scale_fill_manual(values=c(high="#E24B4A",medium="#EF9F27",low="#639922")) +
    labs(title=sprintf("Alert severity — last %d days",days), x="Severity", y="Count") +
    theme_minimal(base_size=13) + theme(legend.position="none")
  ggsave(file.path(CHART_DIR,"alert_severity.png"), p, width=7, height=4, dpi=150)
  p
}

plot_alert_trend <- function(days=30) {
  daily <- alert_daily_trend(days); if(nrow(daily)==0) return(invisible(NULL))
  p <- ggplot(daily, aes(x=day, y=n, colour=severity)) +
    geom_col(aes(fill=severity), alpha=0.35, position="stack") +
    geom_line(aes(y=roll7), linewidth=1) +
    scale_fill_manual(values=c(high="#E24B4A",medium="#EF9F27",low="#639922")) +
    scale_colour_manual(values=c(high="#A32D2D",medium="#BA7517",low="#3B6D11")) +
    scale_x_date(date_breaks="1 week", date_labels="%b %d") +
    labs(title=sprintf("Daily alerts — last %d days (line=7-day avg)",days), x=NULL, y="Alerts") +
    theme_minimal(base_size=13)
  ggsave(file.path(CHART_DIR,"alert_trend.png"), p, width=9, height=4, dpi=150)
  p
}

plot_alert_heatmap <- function(days=30) {
  df <- load_alerts(days); if(nrow(df)==0) return(invisible(NULL))
  heat <- df %>% dplyr::count(wday, hour)
  p <- ggplot(heat, aes(x=hour, y=wday, fill=n)) +
    geom_tile(colour="white", linewidth=0.5) +
    scale_fill_gradient(low="#E6F1FB", high="#185FA5") +
    labs(title="Alert volume by hour and day", x="Hour", y=NULL, fill="Count") +
    theme_minimal(base_size=12)
  ggsave(file.path(CHART_DIR,"alert_heatmap.png"), p, width=9, height=4, dpi=150)
  p
}

if (!interactive()) {
  cat("\n=== FPREN Alert Statistics ===\n")
  print(alert_summary_table(30))
  plot_alert_severity(30); plot_alert_trend(30); plot_alert_heatmap(30)
  cat("Charts saved to", CHART_DIR, "\n")
}
