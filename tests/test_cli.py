import psycopg
import pytest

from omoplab.cli import main
from omoplab.config import load_settings

pytestmark = pytest.mark.db
SCHEMA = "test_cli"


@pytest.fixture
def cli_schema(monkeypatch):
    monkeypatch.setenv("CDM_SCHEMA", SCHEMA)
    settings = load_settings()
    params = {k: v for k, v in settings.items() if k != "schema"}
    try:
        conn = psycopg.connect(**params, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres가 실행 중이 아닙니다 (docker compose up -d)")
    yield
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.commit()
    conn.close()


def test_init_db_refuses_to_overwrite_without_force(cli_schema, capsys):
    main(["init-db"])
    with pytest.raises(SystemExit, match="--force"):
        main(["init-db"])
    main(["init-db", "--force"])

    assert f"스키마 {SCHEMA} 생성" in capsys.readouterr().out


def test_etl_then_status_reports_counts(cli_schema, tmp_path, capsys):
    (tmp_path / "patients.csv").write_text(
        "Id,BIRTHDATE,GENDER,RACE,ETHNICITY\np-a,1980-05-17,F,asian,hispanic\n", encoding="utf-8")
    (tmp_path / "encounters.csv").write_text(
        "Id,START,STOP,PATIENT,ENCOUNTERCLASS\ne1,2020-01-01T09:00:00Z,,p-a,emergency\n", encoding="utf-8")
    (tmp_path / "conditions.csv").write_text("START,STOP,PATIENT,ENCOUNTER,CODE\n2020-01-01,,p-a,e1,123\n", encoding="utf-8")

    main(["init-db"])
    main(["etl", str(tmp_path)])
    main(["status"])

    out = capsys.readouterr().out
    assert "어휘가 비어 있어" in out
    assert "person" in out and "매핑률" in out


def test_load_settings_rejects_reserved_schema(monkeypatch):
    monkeypatch.setenv("CDM_SCHEMA", "public")

    with pytest.raises(SystemExit, match="public"):
        load_settings()
