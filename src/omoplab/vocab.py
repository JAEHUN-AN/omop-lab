"""Athena(https://athena.ohdsi.org)에서 받은 표준 어휘 CSV를 CDM 스키마에 적재한다."""

from pathlib import Path

from omoplab.ddl import ensure_indexes, validate_schema

# 적재 순서: 작은 참조 테이블부터
ATHENA_TABLES = (
    "vocabulary",
    "domain",
    "concept_class",
    "relationship",
    "concept",
    "concept_relationship",
    "concept_synonym",
    "concept_ancestor",
    "drug_strength",
)
# 이 둘이 없으면 매핑 조회가 반쪽짜리가 되므로 적재를 시작하지 않는다
REQUIRED_TABLES = ("concept", "concept_relationship")
_CHUNK_BYTES = 1 << 20


def copy_sql(table: str, schema: str) -> str:
    # Athena 파일은 탭 구분이고, 개념 이름에 따옴표가 그대로 들어 있다.
    # 따옴표 처리를 끄려고 데이터에 나오지 않는 백스페이스를 QUOTE 문자로 지정한다.
    return (
        f"COPY {validate_schema(schema)}.{table} FROM STDIN "
        "WITH (FORMAT csv, DELIMITER E'\\t', HEADER, QUOTE E'\\b')"
    )


def find_vocab_files(vocab_dir: Path) -> dict[str, Path]:
    """CONCEPT.csv처럼 대문자 파일명도 찾는다. 받지 않은 테이블은 빠진다."""
    by_name = {p.stem.lower(): p for p in vocab_dir.glob("*.csv")}
    return {t: by_name[t] for t in ATHENA_TABLES if t in by_name}


def load_vocab(conn, vocab_dir: Path, schema: str, log=print) -> dict[str, int]:
    """한 트랜잭션으로 전부 교체한다. 중간에 실패하면 이전 어휘가 그대로 남는다."""
    schema = validate_schema(schema)
    files = find_vocab_files(vocab_dir)
    missing = [t for t in REQUIRED_TABLES if t not in files]
    if missing:
        raise FileNotFoundError(f"{vocab_dir}에 필수 Athena 파일이 없습니다: {', '.join(missing)}")
    for table in ATHENA_TABLES:
        if table not in files:
            log(f"  - {table}: 파일 없음, 건너뜀")

    counts: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table, path in files.items():
                cur.execute(f"TRUNCATE {schema}.{table}")
                with path.open("rb") as src, cur.copy(copy_sql(table, schema)) as copy:
                    while chunk := src.read(_CHUNK_BYTES):
                        copy.write(chunk)
                counts[table] = cur.rowcount
                log(f"  {table}: {counts[table]:,}행")
        if ensure_indexes(conn, schema):
            log("  PK·인덱스 생성 완료")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts
