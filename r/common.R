# 공통 설정 — 리포 루트에서 `Rscript r/<스크립트>.R`로 실행한다.
#   - 패키지는 리포 전용 라이브러리 .rlib/ 에 둔다 (Python의 .venv와 같은 역할, gitignore)
#   - 접속 정보는 Python 쪽과 같은 .env 를 읽는다

ROOT <- normalizePath(getwd(), winslash = "/")
if (!file.exists(file.path(ROOT, "pyproject.toml"))) stop("리포 루트(omop-lab)에서 실행하세요")

RLIB <- file.path(ROOT, ".rlib")
dir.create(RLIB, showWarnings = FALSE)
.libPaths(c(RLIB, .libPaths()))
JDBC_DIR <- file.path(ROOT, "tools", "jdbc")

read_env <- function(path = file.path(ROOT, ".env")) {
  lines <- readLines(path, encoding = "UTF-8", warn = FALSE)
  lines <- trimws(lines[grepl("^[A-Za-z_][A-Za-z0-9_]*=", lines)])
  values <- trimws(sub("^[^=]*=", "", lines))
  quoted <- grepl("^(\".*\"|'.*')$", values)
  values[quoted] <- substr(values[quoted], 2, nchar(values[quoted]) - 1)  # python-dotenv처럼 따옴표를 벗긴다
  values[!quoted] <- trimws(sub("\\s+#.*$", "", values[!quoted]))       # 따옴표 밖 인라인 주석 제거
  setNames(values, sub("=.*$", "", lines))
}

env_or <- function(env, key, default) if (!is.na(env[key]) && nzchar(env[key])) unname(env[key]) else default

connection_details <- function() {
  env <- read_env()
  if (is.na(env["POSTGRES_PASSWORD"])) stop(".env에 POSTGRES_PASSWORD가 없습니다")
  DatabaseConnector::createConnectionDetails(
    dbms = "postgresql",
    server = sprintf("%s/%s", env_or(env, "PGHOST", "localhost"), env_or(env, "PGDATABASE", "omop")),
    port = as.integer(env_or(env, "PGPORT", "15432")),
    user = env_or(env, "PGUSER", "omop"),
    password = unname(env["POSTGRES_PASSWORD"]),
    pathToDriver = JDBC_DIR
  )
}

CDM_SCHEMA <- env_or(read_env(), "CDM_SCHEMA", "cdm")
