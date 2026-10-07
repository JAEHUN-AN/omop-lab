# OHDSI R 도구 설치: DataQualityDashboard(품질 점검), ETLSyntheaBuilder(공식 Synthea→CDM 변환)
source("r/common.R")
options(repos = c(CRAN = "https://cloud.r-project.org"), timeout = 600)

cran <- c("remotes", "DatabaseConnector", "SqlRender", "ParallelLogger", "jsonlite", "readr", "dplyr")
missing <- setdiff(cran, rownames(installed.packages(lib.loc = RLIB)))
if (length(missing)) install.packages(missing, lib = RLIB, type = "binary")

github <- c(DataQualityDashboard = "OHDSI/DataQualityDashboard", ETLSyntheaBuilder = "OHDSI/ETL-Synthea")
for (pkg in names(github)) {
  if (!pkg %in% rownames(installed.packages(lib.loc = RLIB))) {
    remotes::install_github(github[[pkg]], lib = RLIB, upgrade = "never", dependencies = TRUE, type = "binary")
  }
}

dir.create(JDBC_DIR, recursive = TRUE, showWarnings = FALSE)
if (!length(list.files(JDBC_DIR, pattern = "postgresql.*\\.jar$"))) {
  DatabaseConnector::downloadJdbcDrivers("postgresql", pathToDriver = JDBC_DIR)
}

for (pkg in c("DatabaseConnector", "DataQualityDashboard", "ETLSyntheaBuilder")) {
  cat(sprintf("%-22s %s\n", pkg, as.character(packageVersion(pkg, lib.loc = RLIB))))
}
