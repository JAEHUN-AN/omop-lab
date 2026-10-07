# OHDSI R 도구 설치: DataQualityDashboard(품질 점검), ETLSyntheaBuilder(공식 Synthea→CDM 변환)
source("r/common.R")
options(repos = c(CRAN = "https://cloud.r-project.org"), timeout = 600)

cran <- c("remotes", "DatabaseConnector", "SqlRender", "ParallelLogger", "jsonlite", "readr", "dplyr")
missing <- setdiff(cran, rownames(installed.packages(lib.loc = RLIB)))
if (length(missing)) install.packages(missing, lib = RLIB)

# README의 측정값(DQD 검사 2,384개 등)을 재현하려고 측정에 쓴 커밋으로 고정한다
github <- c(
  DataQualityDashboard = "OHDSI/DataQualityDashboard@9b72f1c8f3db537faa9f59dbe860d8850937aad7",  # v2.9.0
  ETLSyntheaBuilder = "OHDSI/ETL-Synthea@9ee6eb1b933c70af7b80711332aa92327af1f7c5"              # 2.1 (main)
)
for (pkg in names(github)) {
  if (!pkg %in% rownames(installed.packages(lib.loc = RLIB))) {
    remotes::install_github(github[[pkg]], lib = RLIB, upgrade = "never", dependencies = TRUE)
  }
}

dir.create(JDBC_DIR, recursive = TRUE, showWarnings = FALSE)
if (!length(list.files(JDBC_DIR, pattern = "postgresql.*\\.jar$"))) {
  DatabaseConnector::downloadJdbcDrivers("postgresql", pathToDriver = JDBC_DIR)
}

for (pkg in c("DatabaseConnector", "DataQualityDashboard", "ETLSyntheaBuilder")) {
  cat(sprintf("%-22s %s\n", pkg, as.character(packageVersion(pkg, lib.loc = RLIB))))
}
