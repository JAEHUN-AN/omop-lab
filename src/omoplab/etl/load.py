"""Synthea CSV 디렉터리를 읽어 CDM 임상 테이블을 다시 채운다."""

import csv
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple

from omoplab.ddl import validate_schema
from omoplab.etl.concepts import DEFAULT_SYSTEM, lookup_concepts
from omoplab.etl.transform import (
    death_cause_keys,
    to_conditions,
    to_deaths,
    to_observation_periods,
    to_persons,
    to_visits,
)

# CDM 5.4.0 버전 개념 (Metadata 어휘)
CDM_VERSION_CONCEPT_ID = 756265
CDM_SOURCE_SQL = """
INSERT INTO {schema}.cdm_source (cdm_source_name, cdm_source_abbreviation, cdm_holder, source_description,
  cdm_etl_reference, source_release_date, cdm_release_date, cdm_version, cdm_version_concept_id, vocabulary_version)
SELECT 'omop-lab Synthea', 'omoplab', 'omop-lab', 'Synthea 합성 환자 → OMOP CDM 5.4 (학습용 Python ETL)',
  'https://github.com/JAEHUN-AN/omop-lab', CURRENT_DATE, CURRENT_DATE, '5.4', %s,
  COALESCE((SELECT left(vocabulary_version, 20) FROM {schema}.vocabulary WHERE vocabulary_id = 'None'), 'unknown')
"""

CLINICAL_TABLES = ("cdm_source", "death", "observation", "condition_occurrence", "observation_period", "visit_occurrence", "person")


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
    patients = list(read_csv(csv_dir / "patients.csv"))
    encounters = list(read_csv(csv_dir / "encounters.csv"))
    persons, person_ids = to_persons(patients)
    visits, visit_ids = to_visits(encounters, person_ids)
    periods = to_observation_periods(visits)

    condition_rows = list(read_csv(csv_dir / "conditions.csv"))
    keys = {(r.get("SYSTEM") or DEFAULT_SYSTEM, r["CODE"]) for r in condition_rows} | death_cause_keys(encounters)
    lookup = lookup_concepts(conn, schema, keys)
    if not lookup:
        log("  ! 어휘가 비어 있어 진단 개념이 전부 0(미매핑)으로 들어갑니다. load-vocab 후 다시 실행하세요.")
    split = to_conditions(condition_rows, person_ids, visit_ids, lookup)
    deaths = to_deaths(patients, person_ids, encounters, lookup)

    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(f'{schema}.{t}' for t in CLINICAL_TABLES)}")
        for table, rows in (
            ("person", persons),
            ("visit_occurrence", visits),
            ("observation_period", periods),
            ("condition_occurrence", split.conditions),
            ("observation", split.observations),
            ("death", deaths),
        ):
            counts[table] = _copy_rows(cur, schema, table, rows)
            log(f"  {table}: {counts[table]:,}행")
        # 데이터 품질 점검(DQD)이 출처·어휘 버전을 이 테이블에서 읽는다
        cur.execute(CDM_SOURCE_SQL.format(schema=schema), (CDM_VERSION_CONCEPT_ID,))
    conn.commit()
    if split.skipped:
        log(f"  - 진단 중 Condition·Observation 외 도메인 {split.skipped:,}행은 적재하지 않음")
    return counts
