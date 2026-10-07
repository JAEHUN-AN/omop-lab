"""두 CDM 스키마(예: 우리 Python ETL과 공식 ETL-Synthea) 비교.

person_id는 ETL마다 다르게 매기므로, 레코드 대조는 원천 환자 ID(person_source_value)로 맞춘다.
"""

from collections.abc import Sequence
from typing import NamedTuple

from omoplab.ddl import validate_schema

COMPARED_TABLES = (
    "person", "observation_period", "visit_occurrence", "condition_occurrence",
    "observation", "death", "procedure_occurrence", "drug_exposure", "measurement",
)

# (원천 환자, 표준 개념, 시작일) 단위로 대조할 이벤트 테이블
_EVENT_KEYS = {
    "condition_occurrence": ("condition_concept_id", "condition_start_date"),
    "observation": ("observation_concept_id", "observation_date"),
    "visit_occurrence": ("visit_concept_id", "visit_start_date"),
    "death": ("death_type_concept_id", "death_date"),
}


class TableCount(NamedTuple):
    table: str
    left: int
    right: int


class Overlap(NamedTuple):
    table: str
    left: int
    right: int
    both: int


def table_counts(conn, left: str, right: str, tables: Sequence[str] = COMPARED_TABLES) -> list[TableCount]:
    left, right = validate_schema(left), validate_schema(right)
    rows = []
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT (SELECT count(*) FROM {left}.{table}), (SELECT count(*) FROM {right}.{table})")
            rows.append(TableCount(table, *cur.fetchone()))
    return rows


def _event_sql(schema: str, table: str) -> str:
    concept, start = _EVENT_KEYS[table]
    return (f"SELECT p.person_source_value, e.{concept}, e.{start} "
            f"FROM {schema}.{table} e JOIN {schema}.person p ON p.person_id = e.person_id")


def event_overlap(conn, left: str, right: str) -> list[Overlap]:
    """서로 다른 (환자, 개념, 날짜) 조합 기준으로 양쪽에 다 있는 것을 센다."""
    left, right = validate_schema(left), validate_schema(right)
    rows = []
    with conn.cursor() as cur:
        for table in _EVENT_KEYS:
            a, b = _event_sql(left, table), _event_sql(right, table)
            cur.execute(
                f"WITH a AS ({a}), b AS ({b}) SELECT (SELECT count(*) FROM (SELECT DISTINCT * FROM a) x), "
                f"(SELECT count(*) FROM (SELECT DISTINCT * FROM b) y), "
                f"(SELECT count(*) FROM (SELECT * FROM a INTERSECT SELECT * FROM b) z)"
            )
            rows.append(Overlap(table, *cur.fetchone()))
    return rows


def format_comparison(left: str, right: str, counts: Sequence[TableCount], overlaps: Sequence[Overlap]) -> str:
    lines = [f"{'테이블':<22}{left:>14}{right:>14}{'차이':>10}"]
    for c in counts:
        lines.append(f"{c.table:<22}{c.left:>14,}{c.right:>14,}{c.left - c.right:>+10,}")
    lines.append("")
    lines.append(f"{'(환자·개념·날짜)':<22}{left:>14}{right:>14}{'양쪽':>10}{'일치%':>9}")
    for o in overlaps:
        pct = round(100.0 * o.both / o.right, 1) if o.right else 0.0
        lines.append(f"{o.table:<22}{o.left:>14,}{o.right:>14,}{o.both:>10,}{pct:>9}")
    lines.append(f"\n일치%: {right}의 조합 중 {left}에도 있는 비율")
    return "\n".join(lines)
