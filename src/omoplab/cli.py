import argparse
import sys
from pathlib import Path

import psycopg

from omoplab.config import ROOT, load_settings
from omoplab.ddl import apply_ddl, schema_exists, validate_schema
from omoplab.etl.load import run_etl
from omoplab.vocab import load_vocab

# 진단 원천(conditions.csv)은 도메인에 따라 condition_occurrence와 observation으로 나뉘어 들어간다.
# 매핑률은 둘을 합친 원천 행 기준이다 (지금은 observation이 진단에서만 채워진다).
_STATUS_SQL = """
WITH dx AS (
  SELECT condition_concept_id AS concept_id FROM {s}.condition_occurrence
  UNION ALL SELECT observation_concept_id FROM {s}.observation
)
SELECT name, value FROM (
  SELECT 1 AS ord, 'person' AS name, count(*)::text AS value FROM {s}.person
  UNION ALL SELECT 2, 'visit_occurrence', count(*)::text FROM {s}.visit_occurrence
  UNION ALL SELECT 3, 'condition_occurrence', count(*)::text FROM {s}.condition_occurrence
  UNION ALL SELECT 4, 'observation', count(*)::text FROM {s}.observation
  UNION ALL SELECT 5, '진단 원천 매핑률(%)',
         COALESCE(round(100.0 * count(*) FILTER (WHERE concept_id <> 0) / NULLIF(count(*), 0), 1)::text, '-')
    FROM dx
  UNION ALL SELECT 6, 'concept(어휘)', count(*)::text FROM {s}.concept
) t ORDER BY ord
"""


def _connect(settings: dict[str, str]) -> psycopg.Connection:
    params = {k: v for k, v in settings.items() if k != "schema"}
    return psycopg.connect(**params)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omoplab", description="Synthea → OMOP CDM 5.4 학습 도구")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="CDM 스키마를 만든다")
    init.add_argument("--force", action="store_true", help="이미 있는 스키마를 어휘까지 통째로 지우고 다시 만든다")
    vocab = sub.add_parser("load-vocab", help="Athena 어휘 CSV를 적재하고 PK·인덱스를 만든다")
    vocab.add_argument("vocab_dir", type=Path, nargs="?", default=ROOT / "vocab")
    etl = sub.add_parser("etl", help="Synthea CSV를 CDM 임상 테이블로 변환·적재한다")
    etl.add_argument("csv_dir", type=Path, nargs="?", default=ROOT / "data" / "synthea" / "csv")
    sub.add_parser("status", help="테이블 행 수와 진단 매핑률을 보여 준다")
    return parser


def main(argv: list[str] | None = None) -> None:
    # 파이프로 출력할 때 Windows 기본 인코딩(cp949)으로 한글이 깨지지 않게 한다
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)

    settings = load_settings()
    schema = validate_schema(settings["schema"])
    with _connect(settings) as conn:
        if args.command == "init-db":
            if schema_exists(conn, schema) and not args.force:
                raise SystemExit(f"스키마 {schema}가 이미 있습니다. 어휘까지 지우고 다시 만들려면 --force를 붙이세요.")
            apply_ddl(conn, schema)
            print(f"스키마 {schema} 생성 완료 (CDM 5.4)")
        elif args.command == "load-vocab":
            load_vocab(conn, args.vocab_dir, schema)
        elif args.command == "etl":
            run_etl(conn, args.csv_dir, schema)
        elif args.command == "status":
            with conn.cursor() as cur:
                cur.execute(_STATUS_SQL.format(s=schema))
                for name, value in cur.fetchall():
                    print(f"  {name:<24} {value}")


if __name__ == "__main__":
    main()
