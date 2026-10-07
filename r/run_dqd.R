# DataQualityDashboard(OHDSI 공식 데이터 품질 점검)를 CDM 스키마에 돌린다.
#   Rscript r/run_dqd.R            # cdm (우리 Python ETL)
#   Rscript r/run_dqd.R cdm_official  # 공식 ETL-Synthea 결과
# 결과 JSON은 data/dqd/<스키마>.json — DQD 뷰어로 열거나 r/summarize_dqd.R로 요약한다.
source("r/common.R")
suppressPackageStartupMessages(library(DataQualityDashboard))

args <- commandArgs(trailingOnly = TRUE)
schema <- if (length(args)) args[[1]] else CDM_SCHEMA
if (!grepl("^[a-z_][a-z0-9_]*$", schema)) stop("스키마 이름이 올바르지 않습니다: ", schema)

out_dir <- file.path(ROOT, "data", "dqd")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

started <- Sys.time()
results <- executeDqChecks(
  connectionDetails = connection_details(),
  cdmDatabaseSchema = schema,
  resultsDatabaseSchema = schema,
  vocabDatabaseSchema = CDM_SCHEMA,
  cdmSourceName = paste0("omop-lab ", schema),
  cdmVersion = "5.4",
  numThreads = 1,
  outputFolder = out_dir,
  outputFile = paste0(schema, ".json"),
  writeToTable = FALSE,
  verboseMode = FALSE
)
cat(sprintf("완료 %.1f분 → %s\n", as.numeric(difftime(Sys.time(), started, units = "mins")),
            file.path(out_dir, paste0(schema, ".json"))))
o <- results$Overview
cat(sprintf("검사 %d개: 통과 %d, 실패 %d(기준 초과 %d, 오류 %d) — 통과율 %s%%\n",
            o$countTotal, o$countPassed, o$countOverallFailed, o$countThresholdFailed,
            o$countErrorFailed, o$percentPassed))
checks <- results$CheckResults
failed <- checks[checks$failed == 1, intersect(c("category", "checkName", "cdmTableName", "cdmFieldName",
                                                  "numViolatedRows", "numDenominatorRows"), names(checks))]
if (nrow(failed)) print(failed, row.names = FALSE)
