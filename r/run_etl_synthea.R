# 공식 ETL-Synthea(R)로 같은 Synthea CSV를 cdm_official 스키마에 변환한다 — 우리 Python ETL과 비교용.
#
# 어휘는 복사하지 않는다(1억 2천만 행). cdm_official의 어휘 테이블을 cdm 어휘를 가리키는 뷰로 바꾼다.
# 그래서 `omoplab init-db --force`로 cdm을 다시 만들면 이 뷰도 함께 지워진다 → 이 스크립트를 다시 돌린다.
source("r/common.R")
suppressPackageStartupMessages(library(ETLSyntheaBuilder))

OFFICIAL <- "cdm_official"
NATIVE <- "synthea_native"
SYNTHEA_VERSION <- "3.3.0"
CSV_DIR <- file.path(ROOT, "data", "synthea", "csv")
VOCAB_TABLES <- c("concept", "vocabulary", "domain", "concept_class", "concept_relationship",
                  "relationship", "concept_synonym", "concept_ancestor", "drug_strength")

cd <- connection_details()
run_sql <- function(sql) {
  conn <- DatabaseConnector::connect(cd)
  on.exit(DatabaseConnector::disconnect(conn))
  DatabaseConnector::executeSql(conn, sql, progressBar = FALSE, reportOverallTime = FALSE)
}

step <- function(label, expr) {
  started <- Sys.time()
  cat(sprintf("[%s] %s ...\n", format(started, "%H:%M:%S"), label))
  force(expr)
  cat(sprintf("    %.0f초\n", as.numeric(difftime(Sys.time(), started, units = "secs"))))
}

step("스키마 초기화", run_sql(sprintf(
  "DROP SCHEMA IF EXISTS %1$s CASCADE; CREATE SCHEMA %1$s; DROP SCHEMA IF EXISTS %2$s CASCADE; CREATE SCHEMA %2$s;",
  OFFICIAL, NATIVE)))
step("CDM 5.4 테이블", CreateCDMTables(cd, cdmSchema = OFFICIAL, cdmVersion = "5.4", createIndices = FALSE))
step("어휘 테이블 → cdm 뷰", run_sql(paste(sprintf(
  "DROP TABLE %1$s.%2$s; CREATE VIEW %1$s.%2$s AS SELECT * FROM %3$s.%2$s;", OFFICIAL, VOCAB_TABLES, CDM_SCHEMA),
  collapse = "\n")))
step("Synthea 원본 테이블", CreateSyntheaTables(cd, syntheaSchema = NATIVE, syntheaVersion = SYNTHEA_VERSION))
step("Synthea CSV 적재", LoadSyntheaTables(cd, syntheaSchema = NATIVE, syntheaFileLoc = CSV_DIR))
step("매핑·롤업 테이블", CreateMapAndRollupTables(cd, cdmSchema = OFFICIAL, syntheaSchema = NATIVE,
                                                 cdmVersion = "5.4", syntheaVersion = SYNTHEA_VERSION))
if (exists("CreateExtraIndices")) {
  step("보조 인덱스", CreateExtraIndices(cd, cdmSchema = OFFICIAL, syntheaSchema = NATIVE, syntheaVersion = SYNTHEA_VERSION))
}
step("임상 테이블 변환", LoadEventTables(cd, cdmSchema = OFFICIAL, syntheaSchema = NATIVE, cdmVersion = "5.4",
                                         syntheaVersion = SYNTHEA_VERSION, cdmSourceName = "omop-lab Synthea (official ETL)",
                                         cdmSourceAbbreviation = "omoplab-official", cdmHolder = "omop-lab"))
cat("완료: ", OFFICIAL, "\n")
