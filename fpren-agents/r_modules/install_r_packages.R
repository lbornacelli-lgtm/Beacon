pkgs <- c("mongolite","jsonlite","dplyr","tidyr","lubridate","zoo",
          "ggplot2","plotly","scales","rmarkdown","knitr","kableExtra","tinytex")
missing <- pkgs[!pkgs %in% installed.packages()[,"Package"]]
if (length(missing)==0) {
  cat("All packages already installed.\n")
} else {
  cat(sprintf("Installing: %s\n", paste(missing, collapse=", ")))
  install.packages(missing, repos="https://cran.r-project.org", dependencies=TRUE)
}
if (!tinytex::is_tinytex()) { cat("Installing TinyTeX...\n"); tinytex::install_tinytex() }
cat("All FPREN R dependencies ready.\n")
