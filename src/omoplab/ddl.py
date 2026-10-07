"""OHDSI 공식 CDM 5.4 DDL(ddl/, CommonDataModel v5.4.2에서 가져옴)을 스키마 이름으로 렌더링해 적용한다.

FK 제약(constraints.sql)은 적용하지 않는다. 학습 중에 테이블을 자주 비우고 다시 채우기 때문이다.
"""

import re
from pathlib import Path

DDL_DIR = Path(__file__).resolve().parents[2] / "ddl"
DDL_FILES = (
    "OMOPCDM_postgresql_5.4_ddl.sql",
    "OMOPCDM_postgresql_5.4_primary_keys.sql",
    "OMOPCDM_postgresql_5.4_indices.sql",
)
PLACEHOLDER = "@cdmDatabaseSchema"
_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def validate_schema(schema: str) -> str:
    if not _SAFE_IDENTIFIER.match(schema):
        raise ValueError(f"스키마 이름은 소문자·숫자·밑줄만 쓸 수 있습니다: {schema!r}")
    return schema


def render_ddl(text: str, schema: str) -> str:
    return text.replace(PLACEHOLDER, validate_schema(schema))


def apply_ddl(conn, schema: str) -> None:
    """스키마를 새로 만든다. 이미 있으면 통째로 지우고 다시 만든다."""
    schema = validate_schema(schema)
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute(f"CREATE SCHEMA {schema}")
        for name in DDL_FILES:
            cur.execute(render_ddl((DDL_DIR / name).read_text(encoding="utf-8"), schema))
    conn.commit()
