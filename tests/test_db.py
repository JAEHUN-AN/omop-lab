"""실행 중인 Postgres(docker compose up -d)가 있어야 도는 통합 테스트.

본 스키마(cdm)를 건드리지 않도록 test_cdm 스키마를 따로 만들어 쓰고 끝나면 지운다.
"""

import psycopg
import pytest

from omoplab.config import load_settings
from omoplab.ddl import apply_ddl
from omoplab.etl.concepts import ConceptMatch, lookup_concepts
from omoplab.etl.load import run_etl
from omoplab.vocab import load_vocab

pytestmark = pytest.mark.db
SCHEMA = "test_cdm"


@pytest.fixture(scope="module")
def conn():
    settings = load_settings()
    params = {k: v for k, v in settings.items() if k != "schema"}
    try:
        connection = psycopg.connect(**params, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres가 실행 중이 아닙니다 (docker compose up -d)")
    apply_ddl(connection, SCHEMA)
    yield connection
    with connection.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    connection.commit()
    connection.close()


def _write_tsv(path, header, rows):
    lines = ["\t".join(header)] + ["\t".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def vocab_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("vocab")
    concept_header = ["concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id",
                      "standard_concept", "concept_code", "valid_start_date", "valid_end_date", "invalid_reason"]
    _write_tsv(d / "CONCEPT.csv", concept_header, [
        ["201826", "Type 2 diabetes mellitus", "Condition", "SNOMED", "Clinical Finding", "S", "44054006", "19700101", "20991231", ""],
        # 이름 안의 따옴표가 COPY를 깨지 않는지 확인한다
        ["45542411", 'Malignant neoplasm of "base" of tongue', "Condition", "ICD10CM", "4-char billing code", "", "C01", "19700101", "20991231", ""],
        ["4092217", "Malignant tumor of base of tongue", "Condition", "SNOMED", "Clinical Finding", "S", "188154003", "19700101", "20991231", ""],
    ])
    _write_tsv(d / "CONCEPT_RELATIONSHIP.csv",
               ["concept_id_1", "concept_id_2", "relationship_id", "valid_start_date", "valid_end_date", "invalid_reason"], [
        ["201826", "201826", "Maps to", "19700101", "20991231", ""],
        ["45542411", "4092217", "Maps to", "19700101", "20991231", ""],
    ])
    return d


def test_load_vocab_and_lookup_maps_icd10_to_snomed(conn, vocab_dir):
    counts = load_vocab(conn, vocab_dir, SCHEMA, log=lambda _: None)

    assert counts == {"concept": 3, "concept_relationship": 2}
    found = lookup_concepts(conn, SCHEMA, {("ICD10", "C01"), ("SNOMED-CT", "44054006"), ("SNOMED-CT", "nope")})
    assert found == {
        ("ICD10", "C01"): ConceptMatch(45542411, 4092217),
        ("SNOMED-CT", "44054006"): ConceptMatch(201826, 201826),
    }


def test_run_etl_loads_clinical_tables(conn, vocab_dir, tmp_path):
    load_vocab(conn, vocab_dir, SCHEMA, log=lambda _: None)
    (tmp_path / "patients.csv").write_text(
        "Id,BIRTHDATE,GENDER,RACE,ETHNICITY\np-a,1980-05-17,M,white,nonhispanic\n", encoding="utf-8")
    (tmp_path / "encounters.csv").write_text(
        "Id,START,STOP,PATIENT,ENCOUNTERCLASS\ne1,2020-01-01T09:00:00Z,2020-01-01T10:00:00Z,p-a,wellness\n",
        encoding="utf-8")
    (tmp_path / "conditions.csv").write_text(
        "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION\n"
        "2020-01-01,,p-a,e1,ICD10,C01,tongue\n2020-01-01,,p-a,e1,SNOMED-CT,000,unknown\n",
        encoding="utf-8")

    counts = run_etl(conn, tmp_path, SCHEMA, log=lambda _: None)

    assert counts == {"person": 1, "visit_occurrence": 1, "observation_period": 1, "condition_occurrence": 2}
    with conn.cursor() as cur:
        cur.execute(f"SELECT condition_source_value, condition_concept_id, visit_occurrence_id "
                    f"FROM {SCHEMA}.condition_occurrence ORDER BY condition_occurrence_id")
        assert cur.fetchall() == [("C01", 4092217, 1), ("000", 0, 1)]
