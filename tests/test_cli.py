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


def test_measure_prints_summary_and_writes_per_code_csv(cli_schema, tmp_path, capsys):
    main(["init-db"])
    with psycopg.connect(**{k: v for k, v in load_settings().items() if k != "schema"}) as conn:
        conn.execute(
            f"INSERT INTO {SCHEMA}.concept VALUES "
            "(201826,'Type 2 DM','Condition','SNOMED','Clinical Finding','S','44054006','1970-01-01','2099-12-31',NULL),"
            "(1572001,'E11.9','Condition','KCD7','KCD7 code',NULL,'E11.9','1970-01-01','2099-12-31',NULL),"
            "(1599000,'MERS','Condition','KCD7','KCD7 code',NULL,'U19','1970-01-01','2099-12-31',NULL)"
        )
        conn.execute(f"INSERT INTO {SCHEMA}.vocabulary VALUES ('KCD7','KCD','','7th revision',0)")
        conn.execute(
            f"INSERT INTO {SCHEMA}.concept_relationship VALUES "
            "(1572001,201826,'Maps to','1970-01-01','2099-12-31',NULL)"
        )
    codes = tmp_path / "codes.csv"
    codes.write_text("group,code,count\nH1,E119,90\nH1,U19,10\nH2,E1199,3\n", encoding="utf-8")
    out = tmp_path / "result.csv"
    capsys.readouterr()

    main(["measure", str(codes), "--system", "KCD", "--out", str(out)])

    printed = capsys.readouterr().out
    assert "H1" in printed and "H2" in printed and "U19" in printed
    assert "KCD7 7th revision" in printed
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("group,system,raw_code") and len(lines) == 4


def test_measure_fails_when_vocabulary_missing(cli_schema, tmp_path):
    main(["init-db"])
    codes = tmp_path / "codes.csv"
    codes.write_text("code\nE119\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="KCD7"):
        main(["measure", str(codes), "--system", "KCD"])


def test_measure_refuses_paths_that_git_could_pick_up(cli_schema):
    from omoplab.config import ROOT

    with pytest.raises(SystemExit, match="private/"):
        main(["measure", str(ROOT / "codes.csv"), "--system", "KCD"])


def test_measure_reports_missing_file_without_traceback(cli_schema, tmp_path):
    main(["init-db"])

    with pytest.raises(SystemExit, match="측정 실패"):
        main(["measure", str(tmp_path / "nope.csv"), "--system", "KCD"])


def test_load_settings_rejects_reserved_schema(monkeypatch):
    monkeypatch.setenv("CDM_SCHEMA", "public")

    with pytest.raises(SystemExit, match="public"):
        load_settings()
