import argparse
import sys
from pathlib import Path

import psycopg

from omoplab.config import ROOT, load_settings
from omoplab.ddl import apply_ddl, schema_exists, validate_schema
from omoplab.etl.load import run_etl
from omoplab.measure import SYSTEMS, format_report, measure, read_code_list, summarize, write_results
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
    vocab.add_argument(
        "--add", type=Path, action="append", default=[], metavar="DIR",
        help="같은 릴리스로 따로 받은 묶음. 기본 묶음에 없는 어휘만 덧붙인다 (여러 번 지정 가능)",
    )
    etl = sub.add_parser("etl", help="Synthea CSV를 CDM 임상 테이블로 변환·적재한다")
    etl.add_argument("csv_dir", type=Path, nargs="?", default=ROOT / "data" / "synthea" / "csv")
    sub.add_parser("status", help="테이블 행 수와 진단 매핑률을 보여 준다")
    m = sub.add_parser("measure", help="코드 목록 CSV(KCD·EDI)의 표준 개념 연결률을 잰다")
    m.add_argument("csv_path", type=Path)
    m.add_argument("--system", choices=SYSTEMS, help="CSV에 system 열이 없을 때 쓸 코드 체계")
    m.add_argument("--out", type=Path, help="코드별 판정 결과를 저장할 CSV 경로")
    m.add_argument("--top", type=int, default=20, help="표준 개념이 없는 코드를 사용량 순으로 몇 개 보여 줄지")
    return parser


def _warn_if_committable(path: Path) -> None:
    """리포 안이면서 private/ 밖이면 실수로 커밋될 수 있다."""
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT) and not resolved.is_relative_to(ROOT / "private"):
        print(f"  ! {path}는 git에 올라갈 수 있는 위치입니다. 실제 기관 데이터는 private/ 아래에 두세요.")


def _run_measure(conn, schema: str, args: argparse.Namespace) -> None:
    for path in (args.csv_path, args.out):
        if path is not None:
            _warn_if_committable(path)
    try:
        rows = read_code_list(args.csv_path)
        results = measure(conn, schema, rows, args.system)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(f"측정 실패: {e}") from e
    print(format_report(summarize(results), results, args.top))
    if args.out:
        write_results(args.out, results)
        print(f"\n코드별 결과 {len(results):,}행 → {args.out}")


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
            load_vocab(conn, args.vocab_dir, schema, extra_dirs=args.add)
        elif args.command == "etl":
            run_etl(conn, args.csv_dir, schema)
        elif args.command == "measure":
            _run_measure(conn, schema, args)
        elif args.command == "status":
            with conn.cursor() as cur:
                cur.execute(_STATUS_SQL.format(s=schema))
                for name, value in cur.fetchall():
                    print(f"  {name:<24} {value}")


if __name__ == "__main__":
    main()
