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
QUIET = {"log": lambda _: None}

CONCEPT_HEADER = ["concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id",
                  "standard_concept", "concept_code", "valid_start_date", "valid_end_date", "invalid_reason"]
REL_HEADER = ["concept_id_1", "concept_id_2", "relationship_id", "valid_start_date", "valid_end_date", "invalid_reason"]


def _concept(cid, name, domain, vocab, standard, code):
    return [cid, name, domain, vocab, "Clinical Finding", standard, code, "19700101", "20991231", ""]


def _maps_to(src, dst, invalid=""):
    return [src, dst, "Maps to", "19700101", "20991231", invalid]


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
    _write_tsv(d / "CONCEPT.csv", CONCEPT_HEADER, [
        _concept("201826", "Type 2 diabetes mellitus", "Condition", "SNOMED", "S", "44054006"),
        # 이름 안의 따옴표가 COPY를 깨지 않는지 확인한다
        _concept("45542411", 'Malignant neoplasm of "base" of tongue', "Condition", "ICD10CM", "", "C01"),
        _concept("4092217", "Malignant tumor of base of tongue", "Condition", "SNOMED", "S", "188154003"),
        _concept("4200001", "Medication review due", "Observation", "SNOMED", "S", "314529007"),
        # 표준이 아닌 개념을 가리키는 'Maps to'는 무시해야 한다
        _concept("4300001", "Retired finding", "Condition", "SNOMED", "", "111"),
        _concept("4300002", "Non-standard target", "Condition", "SNOMED", "", "222"),
    ])
    _write_tsv(d / "CONCEPT_RELATIONSHIP.csv", REL_HEADER, [
        _maps_to("201826", "201826"),
        _maps_to("45542411", "4092217"),
        _maps_to("4200001", "4200001"),
        _maps_to("4300001", "4300002"),
    ])
    return d


def test_load_vocab_and_lookup_filters_to_standard_targets(conn, vocab_dir):
    counts = load_vocab(conn, vocab_dir, SCHEMA, **QUIET)

    assert counts == {"concept": 6, "concept_relationship": 4}
    found = lookup_concepts(
        conn, SCHEMA,
        {("ICD10", "C01"), ("SNOMED-CT", "44054006"), ("SNOMED-CT", "314529007"), ("SNOMED-CT", "111"), ("SNOMED-CT", "nope")},
    )
    assert found == {
        ("ICD10", "C01"): ConceptMatch(45542411, 4092217, "Condition"),
        ("SNOMED-CT", "44054006"): ConceptMatch(201826, 201826, "Condition"),
        ("SNOMED-CT", "314529007"): ConceptMatch(4200001, 4200001, "Observation"),
        ("SNOMED-CT", "111"): ConceptMatch(4300001, 0, None),
    }


def test_load_vocab_requires_core_files(conn, tmp_path):
    _write_tsv(tmp_path / "CONCEPT.csv", CONCEPT_HEADER, [])

    with pytest.raises(FileNotFoundError, match="concept_relationship"):
        load_vocab(conn, tmp_path, SCHEMA, **QUIET)


def test_load_vocab_supplements_missing_vocabulary_from_other_bundle(conn, vocab_dir, tmp_path):
    extra = tmp_path / "old"
    extra.mkdir()
    (extra / "VOCABULARY.csv").write_text(
        "vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\tvocabulary_concept_id\n"
        "None\tOMOP\t\tv5.0 29-AUG-26\t0\nKCD7\tKCD\t\t7th revision\t0\n", encoding="utf-8")
    _write_tsv(extra / "CONCEPT.csv", CONCEPT_HEADER, [
        _concept("201826", "Type 2 diabetes mellitus", "Condition", "SNOMED", "S", "44054006"),  # 이미 있음 → 중복 금지
        _concept("1572001", "Type 2 diabetes mellitus without complications", "Condition", "KCD7", "", "E11.9"),
    ])
    _write_tsv(extra / "CONCEPT_RELATIONSHIP.csv", REL_HEADER, [
        _maps_to("201826", "201826"),
        _maps_to("1572001", "201826"),
    ])
    (vocab_dir / "VOCABULARY.csv").write_text(
        "vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\tvocabulary_concept_id\n"
        "None\tOMOP\t\tv5.0 29-AUG-26\t0\nSNOMED\tSNOMED\t\ts\t0\n", encoding="utf-8")

    counts = load_vocab(conn, vocab_dir, SCHEMA, extra_dirs=[extra], **QUIET)

    assert counts["supplement:KCD7"] == 1
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.concept WHERE concept_id = 201826")
        assert cur.fetchone()[0] == 1
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.concept_relationship")
        assert cur.fetchone()[0] == 5  # 기본 4 + KCD7→SNOMED 1
        cur.execute(f"SELECT vocabulary_id FROM {SCHEMA}.vocabulary ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == ["KCD7", "None", "SNOMED"]
    (vocab_dir / "VOCABULARY.csv").unlink()


def test_run_etl_routes_conditions_by_domain(conn, vocab_dir, tmp_path):
    load_vocab(conn, vocab_dir, SCHEMA, **QUIET)  # 두 번째 적재도 인덱스 중복 없이 성공해야 한다
    (tmp_path / "patients.csv").write_text(
        "Id,BIRTHDATE,GENDER,RACE,ETHNICITY\np-a,1980-05-17,M,white,nonhispanic\n", encoding="utf-8")
    (tmp_path / "encounters.csv").write_text(
        "Id,START,STOP,PATIENT,ENCOUNTERCLASS\ne1,2020-01-01T09:00:00Z,2020-01-01T10:00:00Z,p-a,wellness\n",
        encoding="utf-8")
    (tmp_path / "conditions.csv").write_text(
        "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION\n"
        "2020-01-01,,p-a,e1,ICD10,C01,tongue\n"
        "2020-01-01,,p-a,e1,SNOMED-CT,000,unknown\n"
        "2020-01-01,,p-a,e1,SNOMED-CT,314529007,Medication review due\n",
        encoding="utf-8")

    counts = run_etl(conn, tmp_path, SCHEMA, **QUIET)

    assert counts == {"person": 1, "visit_occurrence": 1, "observation_period": 1,
                      "condition_occurrence": 2, "observation": 1, "death": 0}
    with conn.cursor() as cur:
        cur.execute(f"SELECT condition_source_value, condition_concept_id, visit_occurrence_id "
                    f"FROM {SCHEMA}.condition_occurrence ORDER BY condition_occurrence_id")
        assert cur.fetchall() == [("C01", 4092217, 1), ("000", 0, 1)]
        cur.execute(f"SELECT observation_concept_id, observation_source_value FROM {SCHEMA}.observation")
        assert cur.fetchall() == [(4200001, "314529007")]
        cur.execute(f"SELECT cdm_version, vocabulary_version FROM {SCHEMA}.cdm_source")
        ((cdm_version, vocabulary_version),) = cur.fetchall()
        # 어휘 버전은 앞선 테스트가 넣은 VOCABULARY의 'None' 행에서 오거나, 없으면 'unknown'
        assert cdm_version == "5.4" and vocabulary_version in ("unknown", "v5.0 29-AUG-26")
