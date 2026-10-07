from pathlib import Path

import pytest

from omoplab.ddl import DDL_FILES, render_ddl
from omoplab.vocab import ATHENA_TABLES, copy_sql, find_vocab_files


def test_render_ddl_replaces_schema_placeholder():
    assert render_ddl("CREATE TABLE @cdmDatabaseSchema.person (x int);", "cdm") == "CREATE TABLE cdm.person (x int);"


def test_render_ddl_rejects_unsafe_schema_name():
    with pytest.raises(ValueError):
        render_ddl("CREATE TABLE @cdmDatabaseSchema.person (x int);", "cdm; DROP TABLE x")


def test_ddl_files_exist_in_order():
    root = Path(__file__).resolve().parents[1] / "ddl"
    assert [f.split("_")[-1] for f in DDL_FILES] == ["ddl.sql", "keys.sql", "indices.sql"]
    assert all((root / f).exists() for f in DDL_FILES)


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
