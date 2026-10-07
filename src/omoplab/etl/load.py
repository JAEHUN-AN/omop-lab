"""Synthea CSV 디렉터리를 읽어 CDM 임상 테이블을 다시 채운다."""

import csv
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple

from omoplab.ddl import validate_schema
from omoplab.etl.concepts import DEFAULT_SYSTEM, lookup_concepts
from omoplab.etl.transform import to_conditions, to_observation_periods, to_persons, to_visits

CLINICAL_TABLES = ("observation", "condition_occurrence", "observation_period", "visit_occurrence", "person")


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _copy_rows(cur, schema: str, table: str, rows: Sequence[NamedTuple]) -> int:
    if not rows:
        return 0
    columns = ", ".join(rows[0]._fields)
    with cur.copy(f"COPY {schema}.{table} ({columns}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    return len(rows)


def run_etl(conn, csv_dir: Path, schema: str, log=print) -> dict[str, int]:
    schema = validate_schema(schema)
    persons, person_ids = to_persons(read_csv(csv_dir / "patients.csv"))
    visits, visit_ids = to_visits(read_csv(csv_dir / "encounters.csv"), person_ids)
    periods = to_observation_periods(visits)

    condition_rows = list(read_csv(csv_dir / "conditions.csv"))
    keys = {(r.get("SYSTEM") or DEFAULT_SYSTEM, r["CODE"]) for r in condition_rows}
    lookup = lookup_concepts(conn, schema, keys)
    if not lookup:
        log("  ! 어휘가 비어 있어 진단 개념이 전부 0(미매핑)으로 들어갑니다. load-vocab 후 다시 실행하세요.")
    split = to_conditions(condition_rows, person_ids, visit_ids, lookup)

    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(f'{schema}.{t}' for t in CLINICAL_TABLES)}")
        for table, rows in (
            ("person", persons),
            ("visit_occurrence", visits),
            ("observation_period", periods),
            ("condition_occurrence", split.conditions),
            ("observation", split.observations),
        ):
            counts[table] = _copy_rows(cur, schema, table, rows)
            log(f"  {table}: {counts[table]:,}행")
    conn.commit()
    if split.skipped:
        log(f"  - 진단 중 Condition·Observation 외 도메인 {split.skipped:,}행은 적재하지 않음")
    return counts
