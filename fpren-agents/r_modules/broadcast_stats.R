source(file.path(dirname(sys.frame(1)$ofile %||% "."), "fpren_mongo.R"))
suppressPackageStartupMessages({ library(ggplot2); library(scales) })

CHART_DIR <- file.path(getwd(), "reports", "charts")
dir.create(CHART_DIR, recursive=TRUE, showWarnings=FALSE)

load_tts <- function(days=30) {
  df <- pull_recent(col_tts, days=days)
  if (nrow(df)==0) return(df)
  df <- parse_ts(df)
  df$day      <- as.Date(df$inserted_at)
  df$category <- as.character(df$category)
  df$chars    <- as.numeric(df$char_count)
  df$priority <- as.integer(df$priority)
  df
}

char_usage_summary <- function(days=30) {
  df <- load_tts(days); if(nrow(df)==0) return(data.frame())
  df %>% dplyr::group_by(category) %>%
    dplyr::summarise(total_chars=sum(chars,na.rm=TRUE),
                     total_clips=dplyr::n(),
                     avg_chars=round(mean(chars,na.rm=TRUE),0), .groups="drop") %>%
    dplyr::arrange(dplyr::desc(total_chars))
}

plot_char_usage <- function(days=30) {
  df <- char_usage_summary(days); if(nrow(df)==0) return(invisible(NULL))
  p <- ggplot(df, aes(x=reorder(category,total_chars), y=total_chars, fill=category)) +
    geom_col(width=0.65) + coord_flip() +
    scale_fill_manual(values=c(weather="#378ADD",traffic="#EF9F27",alerts="#E24B4A",default="#888780")) +
    scale_y_continuous(labels=scales::comma, expand=expansion(mult=c(0,0.15))) +
    labs(title=sprintf("ElevenLabs char usage — last %d days",days),
         x=NULL, y="Characters", caption="Proxy for API cost") +
    theme_minimal(base_size=13) + theme(legend.position="none")
  ggsave(file.path(CHART_DIR,"broadcast_char_usage.png"), p, width=7, height=4, dpi=150)
  p
}

plot_broadcast_volume <- function(days=30) {
  df <- load_tts(days); if(nrow(df)==0) return(invisible(NULL))
  daily <- df %>% dplyr::count(day, category) %>%
    tidyr::complete(day, category, fill=list(n=0))
  p <- ggplot(daily, aes(x=day, y=n, fill=category)) + geom_col(width=0.85) +
    scale_fill_manual(values=c(weather="#378ADD",traffic="#EF9F27",alerts="#E24B4A",default="#888780")) +
    scale_x_date(date_breaks="1 week", date_labels="%b %d") +
    labs(title=sprintf("Daily broadcast clips — last %d days",days),
         x=NULL, y="Clips", fill=NULL) +
    theme_minimal(base_size=13) + theme(legend.position="top")
  ggsave(file.path(CHART_DIR,"broadcast_volume.png"), p, width=9, height=4, dpi=150)
  p
}

if (!interactive()) {
  cat("\n=== FPREN Broadcast Statistics ===\n")
  print(char_usage_summary(30))
  plot_char_usage(30); plot_broadcast_volume(30)
  cat("Charts saved to", CHART_DIR, "\n")
}
