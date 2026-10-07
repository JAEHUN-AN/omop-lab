import argparse
import sys
from pathlib import Path

import psycopg

from omoplab.config import ROOT, load_settings
from omoplab.ddl import apply_ddl
from omoplab.etl.load import run_etl
from omoplab.vocab import load_vocab

_STATUS_SQL = """
SELECT 'person', count(*) FROM {s}.person
UNION ALL SELECT 'visit_occurrence', count(*) FROM {s}.visit_occurrence
UNION ALL SELECT 'condition_occurrence', count(*) FROM {s}.condition_occurrence
UNION ALL SELECT 'condition 매핑률(%)',
       round(100.0 * count(*) FILTER (WHERE condition_concept_id <> 0) / NULLIF(count(*), 0), 1)
  FROM {s}.condition_occurrence
UNION ALL SELECT 'concept(어휘)', count(*) FROM {s}.concept
"""


def _connect(settings: dict[str, str]) -> psycopg.Connection:
    params = {k: v for k, v in settings.items() if k != "schema"}
    return psycopg.connect(**params)


def main(argv: list[str] | None = None) -> None:
    # 파이프로 출력할 때 Windows 기본 인코딩(cp949)으로 한글이 깨지지 않게 한다
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="omoplab", description="Synthea → OMOP CDM 5.4 학습 도구")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="CDM 스키마를 (다시) 만든다 — 기존 데이터는 지워진다")
    vocab = sub.add_parser("load-vocab", help="Athena 어휘 CSV를 적재한다")
    vocab.add_argument("vocab_dir", type=Path, nargs="?", default=ROOT / "vocab")
    etl = sub.add_parser("etl", help="Synthea CSV를 CDM 임상 테이블로 변환·적재한다")
    etl.add_argument("csv_dir", type=Path, nargs="?", default=ROOT / "data" / "synthea" / "csv")
    sub.add_parser("status", help="테이블 행 수와 진단 매핑률을 보여 준다")
    args = parser.parse_args(argv)

    settings = load_settings()
    schema = settings["schema"]
    with _connect(settings) as conn:
        if args.command == "init-db":
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
