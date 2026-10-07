from pathlib import Path

import pytest

from omoplab.ddl import INDEX_FILES, TABLE_FILE, render_ddl, validate_schema
from omoplab.vocab import ATHENA_TABLES, copy_sql, find_vocab_files


def test_render_ddl_replaces_schema_placeholder():
    assert render_ddl("CREATE TABLE @cdmDatabaseSchema.person (x int);", "cdm") == "CREATE TABLE cdm.person (x int);"


@pytest.mark.parametrize(
    "bad",
    ["cdm; DROP TABLE x", "cdm\n", "Cdm", "public", "information_schema", "pg_temp", "a" * 64, ""],
)
def test_validate_schema_rejects_unsafe_or_reserved_names(bad):
    with pytest.raises(ValueError):
        validate_schema(bad)


def test_validate_schema_accepts_plain_name():
    assert validate_schema("test_cdm2") == "test_cdm2"


def test_ddl_files_exist():
    root = Path(__file__).resolve().parents[1] / "ddl"
    assert TABLE_FILE.endswith("_ddl.sql")
    assert [f.split("_")[-1] for f in INDEX_FILES] == ["keys.sql", "indices.sql"]
    assert all((root / f).exists() for f in (TABLE_FILE, *INDEX_FILES))


def test_copy_sql_reads_athena_tab_files_without_quote_handling():
    sql = copy_sql("concept", "cdm")

    assert sql.startswith("COPY cdm.concept FROM STDIN")
    assert "DELIMITER E'\\t'" in sql and "HEADER" in sql
    # Athena 파일은 이름에 따옴표가 섞여 있어 따옴표 처리를 꺼야 한다
    assert "QUOTE E'\\b'" in sql


def test_find_vocab_files_matches_case_insensitively(tmp_path):
    (tmp_path / "CONCEPT.csv").write_text("x")
    (tmp_path / "vocabulary.csv").write_text("x")

    found = find_vocab_files(tmp_path)

    assert set(found) == {"concept", "vocabulary"}
    assert "concept_relationship" in ATHENA_TABLES
