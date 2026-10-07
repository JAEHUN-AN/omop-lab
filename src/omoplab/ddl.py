"""OHDSI 공식 CDM 5.4 DDL(ddl/, CommonDataModel v5.4.2에서 가져옴)을 스키마 이름으로 렌더링해 적용한다.

- 테이블은 init-db에서, PK·인덱스는 어휘 적재 뒤에 만든다 (OHDSI 권장 순서 — 수천만 행 COPY가 훨씬 빠르다).
- FK 제약(constraints.sql)은 적용하지 않는다. 학습 중에 테이블을 자주 비우고 다시 채우기 때문이다.
"""

import re
from pathlib import Path

DDL_DIR = Path(__file__).resolve().parents[2] / "ddl"
TABLE_FILE = "OMOPCDM_postgresql_5.4_ddl.sql"
INDEX_FILES = (
    "OMOPCDM_postgresql_5.4_primary_keys.sql",
    "OMOPCDM_postgresql_5.4_indices.sql",
)
PLACEHOLDER = "@cdmDatabaseSchema"
_SAFE_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")
# init-db가 DROP SCHEMA ... CASCADE를 하므로 시스템·공용 스키마는 막는다
_RESERVED = {"public", "information_schema"}


def validate_schema(schema: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(schema):
        raise ValueError(f"스키마 이름은 소문자·숫자·밑줄 63자 이하만 쓸 수 있습니다: {schema!r}")
    if schema in _RESERVED or schema.startswith("pg_"):
        raise ValueError(f"예약된 스키마는 쓸 수 없습니다: {schema!r}")
    return schema


def render_ddl(text: str, schema: str) -> str:
    return text.replace(PLACEHOLDER, validate_schema(schema))


def schema_exists(conn, schema: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (validate_schema(schema),))
        return cur.fetchone() is not None


def apply_ddl(conn, schema: str) -> None:
    """스키마를 새로 만든다. 이미 있으면 통째로 지우고 다시 만든다 (어휘 포함)."""
    schema = validate_schema(schema)
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(render_ddl((DDL_DIR / TABLE_FILE).read_text(encoding="utf-8"), schema))
    conn.commit()


def ensure_indexes(conn, schema: str) -> bool:
    """PK·인덱스가 아직 없으면 만든다. 새로 만들었으면 True."""
    schema = validate_schema(schema)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_indexes WHERE schemaname = %s", (schema,))
        if cur.fetchone()[0] > 0:
            return False
        for name in INDEX_FILES:
            cur.execute(render_ddl((DDL_DIR / name).read_text(encoding="utf-8"), schema))
    return True
