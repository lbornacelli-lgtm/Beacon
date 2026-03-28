library(shiny)
library(shinydashboard)
library(mongolite)
library(DT)
library(dplyr)
library(lubridate)

# ── MongoDB connections ───────────────────────────────────────────────────────
MONGO_URI <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")

get_col <- function(collection) {
  tryCatch(
    mongo(collection = collection, db = "weather_rss", url = MONGO_URI),
    error = function(e) NULL
  )
}

# ── UI ────────────────────────────────────────────────────────────────────────
ui <- dashboardPage(
  skin = "blue",

  dashboardHeader(title = "FPREN Weather Station"),

  dashboardSidebar(
    sidebarMenu(
      menuItem("Overview",        tabName = "overview",  icon = icon("tachometer-alt")),
      menuItem("FL Alerts",       tabName = "alerts",    icon = icon("exclamation-triangle")),
      menuItem("Alachua County",  tabName = "alachua",   icon = icon("map-marker-alt")),
      menuItem("Airport Delays",  tabName = "airports",  icon = icon("plane")),
      menuItem("Station Health",  tabName = "health",    icon = icon("heartbeat")),
      menuItem("Feed Status",     tabName = "feeds",     icon = icon("rss"))
    )
  ),

  dashboardBody(
    tags$head(tags$style(HTML("
      .content-wrapper { background-color: #f4f6f9; }
      .small-box .icon { font-size: 60px; }
      .alert-extreme { background-color: #f56954 !important; color: white !important; }
      .alert-severe  { background-color: #f39c12 !important; color: white !important; }
    "))),

    tabItems(

      # ── Overview ────────────────────────────────────────────────────────────
      tabItem(tabName = "overview",
        fluidRow(
          valueBoxOutput("box_fl_alerts",      width = 3),
          valueBoxOutput("box_alachua_alerts",  width = 3),
          valueBoxOutput("box_airport_delays",  width = 3),
          valueBoxOutput("box_wavs_generated",  width = 3)
        ),
        fluidRow(
          box(title = "Active Florida Alerts", width = 8, status = "danger",
              solidHeader = TRUE, DTOutput("tbl_overview_alerts")),
          box(title = "Station Status", width = 4, status = "info",
              solidHeader = TRUE,
              h4("TTS Engine"),
              verbatimTextOutput("txt_tts_engine"),
              hr(),
              h4("Last Heartbeat"),
              verbatimTextOutput("txt_heartbeat"),
              hr(),
              h4("MongoDB"),
              verbatimTextOutput("txt_mongo_status"))
        ),
        fluidRow(
          box(title = "Auto-refresh", width = 12, status = "primary",
              checkboxInput("auto_refresh", "Auto-refresh every 60 seconds", value = TRUE))
        )
      ),

      # ── FL Alerts ───────────────────────────────────────────────────────────
      tabItem(tabName = "alerts",
        fluidRow(
          box(title = "Active Florida NWS Alerts", width = 12, status = "danger",
              solidHeader = TRUE,
              fluidRow(
                column(4, selectInput("alert_severity", "Filter by Severity",
                  choices = c("All", "Extreme", "Severe", "Moderate", "Minor"),
                  selected = "All")),
                column(4, selectInput("alert_source", "Filter by Source",
                  choices = c("All", "IPAWS", "NWS"),
                  selected = "All")),
                column(4, br(), actionButton("btn_refresh_alerts", "Refresh",
                  class = "btn-primary"))
              ),
              DTOutput("tbl_alerts"))
        )
      ),

      # ── Alachua County ──────────────────────────────────────────────────────
      tabItem(tabName = "alachua",
        fluidRow(
          valueBoxOutput("box_alachua_active",  width = 4),
          valueBoxOutput("box_alachua_extreme", width = 4),
          valueBoxOutput("box_alachua_wavs",    width = 4)
        ),
        fluidRow(
          box(title = "Alachua County Alerts", width = 12, status = "warning",
              solidHeader = TRUE, DTOutput("tbl_alachua"))
        )
      ),

      # ── Airport Delays ──────────────────────────────────────────────────────
      tabItem(tabName = "airports",
        fluidRow(
          valueBoxOutput("box_airports_delayed", width = 4),
          valueBoxOutput("box_airports_ok",      width = 4),
          valueBoxOutput("box_airports_total",   width = 4)
        ),
        fluidRow(
          box(title = "Florida Airport Status", width = 12, status = "info",
              solidHeader = TRUE, DTOutput("tbl_airports"))
        )
      ),

      # ── Station Health ──────────────────────────────────────────────────────
      tabItem(tabName = "health",
        fluidRow(
          box(title = "Recent WAV/MP3 Files Generated", width = 8, status = "success",
              solidHeader = TRUE, DTOutput("tbl_wavs")),
          box(title = "System Info", width = 4, status = "primary",
              solidHeader = TRUE,
              h4("Shiny Server"),
              p(icon("check-circle", style="color:green"), " Running on port 3838"),
              hr(),
              h4("R Version"),
              verbatimTextOutput("txt_r_version"),
              hr(),
              h4("MongoDB Collections"),
              verbatimTextOutput("txt_collections"),
              hr(),
              h4("Last Dashboard Refresh"),
              verbatimTextOutput("txt_last_refresh"))
        )
      ),

      # ── Feed Status ─────────────────────────────────────────────────────────
      tabItem(tabName = "feeds",
        fluidRow(
          box(title = "Feed Health Status", width = 12, status = "primary",
              solidHeader = TRUE, DTOutput("tbl_feeds"))
        )
      )
    )
  )
)

# ── Server ────────────────────────────────────────────────────────────────────
server <- function(input, output, session) {

  # Auto-refresh timer
  timer <- reactiveTimer(60000)

  # ── Data loaders ────────────────────────────────────────────────────────────

  alerts_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("nws_alerts")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"alert_id":1,"event":1,"headline":1,
        "severity":1,"area_desc":1,"source":1,"sent":1,
        "alachua_county":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  airport_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("airport_delays")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"icao":1,"name":1,"state":1,
        "has_delay":1,"fetched_at":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  wav_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("zone_alert_wavs")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"source_type":1,"zone":1,"event":1,
        "tts_engine":1,"generated_at":1,"wav_path":1,"_id":0}',
        sort = '{"generated_at":-1}', limit = 50)
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  feed_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("feed_status")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"filename":1,"status":1,
        "last_checked":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  # ── Overview boxes ──────────────────────────────────────────────────────────

  output$box_fl_alerts <- renderValueBox({
    df <- alerts_data()
    n  <- if (nrow(df) == 0) 0 else nrow(df)
    valueBox(n, "FL Alerts Active", icon = icon("exclamation-triangle"),
             color = if (n > 0) "red" else "green")
  })

  output$box_alachua_alerts <- renderValueBox({
    df <- alerts_data()
    n  <- if (nrow(df) == 0 || !"alachua_county" %in% names(df)) 0
          else sum(df$alachua_county == TRUE, na.rm = TRUE)
    valueBox(n, "Alachua Alerts", icon = icon("map-marker-alt"),
             color = if (n > 0) "orange" else "green")
  })

  output$box_airport_delays <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0 || !"has_delay" %in% names(df)) 0
          else sum(df$has_delay == TRUE, na.rm = TRUE)
    valueBox(n, "Airport Delays", icon = icon("plane"),
             color = if (n > 0) "yellow" else "green")
  })

  output$box_wavs_generated <- renderValueBox({
    df <- wav_data()
    valueBox(nrow(df), "Recent Audio Files", icon = icon("volume-up"),
             color = "blue")
  })

  output$tbl_overview_alerts <- renderDT({
    df <- alerts_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No active alerts")))
    df <- df %>% select(any_of(c("event","severity","area_desc","source","sent")))
    datatable(df, options = list(pageLength = 5, scrollX = TRUE),
              rownames = FALSE)
  })

  output$txt_tts_engine <- renderText({ "ElevenLabs via LiteLLM" })

  output$txt_heartbeat <- renderText({
    path <- "/home/ufuser/Fpren-main/watchdog.heartbeat"
    if (file.exists(path)) {
      mtime <- file.mtime(path)
      age   <- round(as.numeric(difftime(Sys.time(), mtime, units = "mins")), 1)
      paste0(format(mtime, "%Y-%m-%d %H:%M:%S"), "\n(", age, " minutes ago)")
    } else {
      "Heartbeat file not found"
    }
  })

  output$txt_mongo_status <- renderText({
    col <- get_col("nws_alerts")
    if (is.null(col)) "MongoDB: OFFLINE" else {
      col$disconnect()
      "MongoDB: ONLINE"
    }
  })

  # ── FL Alerts tab ──────────────────────────────────────────────────────────

  output$tbl_alerts <- renderDT({
    df <- alerts_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No alerts found")))

    if (input$alert_severity != "All" && "severity" %in% names(df))
      df <- df %>% filter(tolower(severity) == tolower(input$alert_severity))
    if (input$alert_source != "All" && "source" %in% names(df))
      df <- df %>% filter(source == input$alert_source)

    df <- df %>% select(any_of(c("event","severity","headline","area_desc",
                                  "source","alachua_county","sent")))
    datatable(df, options = list(pageLength = 10, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("severity",
        backgroundColor = styleEqual(
          c("Extreme","Severe","Moderate"),
          c("#f56954",  "#f39c12", "#f0ad4e")))
  })

  observeEvent(input$btn_refresh_alerts, { alerts_data() })

  # ── Alachua tab ─────────────────────────────────────────────────────────────

  alachua_df <- reactive({
    df <- alerts_data()
    if (nrow(df) == 0 || !"alachua_county" %in% names(df)) return(data.frame())
    df %>% filter(alachua_county == TRUE)
  })

  output$box_alachua_active <- renderValueBox({
    valueBox(nrow(alachua_df()), "Active Alachua Alerts",
             icon = icon("exclamation-circle"),
             color = if (nrow(alachua_df()) > 0) "red" else "green")
  })

  output$box_alachua_extreme <- renderValueBox({
    df <- alachua_df()
    n  <- if (nrow(df) == 0 || !"severity" %in% names(df)) 0
          else sum(tolower(df$severity) %in% c("extreme","severe"), na.rm = TRUE)
    valueBox(n, "Extreme/Severe", icon = icon("bolt"),
             color = if (n > 0) "red" else "green")
  })

  output$box_alachua_wavs <- renderValueBox({
    df <- wav_data()
    # Count WAVs for all_florida or north_florida zones (Alachua is north FL)
    n  <- if (nrow(df) == 0) 0
          else nrow(df %>% filter(zone %in% c("all_florida","north_florida")))
    valueBox(n, "Zone Audio Files", icon = icon("file-audio"), color = "blue")
  })

  output$tbl_alachua <- renderDT({
    df <- alachua_df()
    if (nrow(df) == 0)
      return(datatable(data.frame(Message = "No active Alachua County alerts")))
    df <- df %>% select(any_of(c("event","severity","headline","area_desc","sent","source")))
    datatable(df, options = list(pageLength = 10, scrollX = TRUE), rownames = FALSE)
  })

  # ── Airport tab ─────────────────────────────────────────────────────────────

  output$box_airports_delayed <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0) 0 else sum(df$has_delay == TRUE, na.rm = TRUE)
    valueBox(n, "Airports Delayed", icon = icon("exclamation-circle"),
             color = if (n > 0) "red" else "green")
  })

  output$box_airports_ok <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0) 0 else sum(df$has_delay == FALSE, na.rm = TRUE)
    valueBox(n, "Airports Normal", icon = icon("check-circle"), color = "green")
  })

  output$box_airports_total <- renderValueBox({
    valueBox(nrow(airport_data()), "Airports Monitored",
             icon = icon("globe"), color = "blue")
  })

  output$tbl_airports <- renderDT({
    df <- airport_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No airport data")))
    df <- df %>%
      mutate(status = ifelse(has_delay == TRUE, "DELAYED", "Normal")) %>%
      select(any_of(c("icao","name","state","status","fetched_at"))) %>%
      arrange(desc(status))
    datatable(df, options = list(pageLength = 20, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("status",
        color = styleEqual(c("DELAYED","Normal"), c("red","green")),
        fontWeight = styleEqual("DELAYED", "bold"))
  })

  # ── Station health tab ──────────────────────────────────────────────────────

  output$tbl_wavs <- renderDT({
    df <- wav_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No audio files found")))
    df <- df %>% select(any_of(c("source_type","zone","event","tts_engine","generated_at")))
    datatable(df, options = list(pageLength = 15, scrollX = TRUE), rownames = FALSE)
  })

  output$txt_r_version  <- renderText({ R.version.string })
  output$txt_last_refresh <- renderText({ format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC") })

  output$txt_collections <- renderText({
    cols <- c("nws_alerts","airport_delays","zone_alert_wavs","feed_status")
    results <- sapply(cols, function(c) {
      col <- get_col(c)
      if (is.null(col)) paste0(c, ": ERROR")
      else {
        n <- tryCatch({ col$count(); }, error = function(e) "?")
        col$disconnect()
        paste0(c, ": ", n, " docs")
      }
    })
    paste(results, collapse = "\n")
  })

  # ── Feed status tab ─────────────────────────────────────────────────────────

  output$tbl_feeds <- renderDT({
    df <- feed_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No feed status data")))
    datatable(df, options = list(pageLength = 20, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("status",
        color      = styleEqual(c("OK","ERROR"), c("green","red")),
        fontWeight = styleEqual("ERROR", "bold"))
  })

  # ── Auto-refresh ────────────────────────────────────────────────────────────
  observe({
    if (input$auto_refresh) timer()
    output$txt_last_refresh <- renderText({
      format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC")
    })
  })
}

shinyApp(ui, server)
