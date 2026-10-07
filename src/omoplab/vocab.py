"""Athena(https://athena.ohdsi.org)에서 받은 표준 어휘 CSV를 CDM 스키마에 적재한다."""

from pathlib import Path

from omoplab.ddl import ensure_indexes, validate_schema

# 적재 순서: 작은 참조 테이블부터
ATHENA_TABLES = (
    "vocabulary",
    "domain",
    "concept_class",
    "relationship",
    "concept",
    "concept_relationship",
    "concept_synonym",
    "concept_ancestor",
    "drug_strength",
)
# 이 둘이 없으면 매핑 조회가 반쪽짜리가 되므로 적재를 시작하지 않는다
REQUIRED_TABLES = ("concept", "concept_relationship")
_CHUNK_BYTES = 1 << 20
# VOCABULARY.csv에서 Athena 릴리스 버전이 적힌 행 (예: "v5.0 29-AUG-26")
RELEASE_KEY = "None"
_SUPPLEMENT_TABLES = ("vocabulary", "concept", "concept_relationship", "concept_synonym")
# Athena 파일은 탭 구분이고, 개념 이름에 따옴표가 그대로 들어 있다.
# 따옴표 처리를 끄려고 데이터에 나오지 않는 백스페이스를 QUOTE 문자로 지정한다.
_COPY_OPTIONS = "WITH (FORMAT csv, DELIMITER E'\\t', HEADER, QUOTE E'\\b')"


def copy_sql(table: str, schema: str) -> str:
    return f"COPY {validate_schema(schema)}.{table} FROM STDIN {_COPY_OPTIONS}"


def find_vocab_files(vocab_dir: Path) -> dict[str, Path]:
    """CONCEPT.csv처럼 대문자 파일명도 찾는다. 받지 않은 테이블은 빠진다."""
    by_name = {p.stem.lower(): p for p in vocab_dir.glob("*.csv")}
    return {t: by_name[t] for t in ATHENA_TABLES if t in by_name}


def read_vocabularies(vocab_dir: Path) -> dict[str, str]:
    """VOCABULARY.csv → {vocabulary_id: vocabulary_version}."""
    path = find_vocab_files(vocab_dir).get("vocabulary")
    if path is None:
        raise FileNotFoundError(f"{vocab_dir}에 VOCABULARY.csv가 없습니다")
    with path.open(encoding="utf-8-sig") as f:
        next(f)  # 헤더
        rows = (line.rstrip("\r\n").split("\t") for line in f if line.strip())
        return {cols[0]: cols[3] for cols in rows}


def plan_supplement(primary_dir: Path, extra_dir: Path) -> tuple[str, ...]:
    """extra 묶음에만 있는 어휘 목록. 두 묶음의 Athena 릴리스가 다르면 섞지 않는다.

    Athena는 한 번에 받은 묶음 안에서만 개념·관계가 서로 맞물린다.
    릴리스가 같으면 어휘별로 나눠 받은 묶음을 합쳐도 일관성이 유지된다.
    """
    primary, extra = read_vocabularies(primary_dir), read_vocabularies(extra_dir)
    release, extra_release = primary.get(RELEASE_KEY), extra.get(RELEASE_KEY)
    if release != extra_release:
        raise ValueError(f"Athena 릴리스가 다릅니다: {release!r} vs {extra_release!r}. 같은 릴리스로 다시 받으세요.")
    return tuple(sorted(set(extra) - set(primary)))


def _copy_file(cur, path: Path, target: str) -> int:
    with path.open("rb") as src, cur.copy(f"COPY {target} FROM STDIN {_COPY_OPTIONS}") as copy:
        while chunk := src.read(_CHUNK_BYTES):
            copy.write(chunk)
    return cur.rowcount


def _supplement(cur, extra_dir: Path, schema: str, vocabulary_ids: tuple[str, ...], log) -> dict[str, int]:
    """extra 묶음에서 지정한 어휘의 개념과, 그 개념이 한쪽 끝에 걸린 관계·동의어만 덧붙인다.

    기본 묶음에 없는 어휘이므로 그 개념 ID와 관계는 기본 묶음과 겹치지 않는다.
    concept_ancestor는 표준 개념끼리만 있어서 비표준 어휘(KCD7, ICD10 등)에는 필요 없다.
    """
    files = find_vocab_files(extra_dir)
    if "concept" not in files:
        raise FileNotFoundError(f"{extra_dir}에 CONCEPT.csv가 없습니다")
    staged = [t for t in _SUPPLEMENT_TABLES if t in files]
    counts: dict[str, int] = {}
    for table in staged:
        cur.execute(f"CREATE TEMP TABLE _s_{table} (LIKE {schema}.{table})")
        _copy_file(cur, files[table], f"_s_{table}")

    ids = list(vocabulary_ids)
    cur.execute(f"INSERT INTO {schema}.vocabulary SELECT * FROM _s_vocabulary WHERE vocabulary_id = ANY(%s)", (ids,))
    cur.execute(
        "CREATE TEMP TABLE _s_new AS "
        "SELECT concept_id, vocabulary_id, standard_concept FROM _s_concept WHERE vocabulary_id = ANY(%s)",
        (ids,),
    )
    cur.execute(f"INSERT INTO {schema}.concept SELECT c.* FROM _s_concept c JOIN _s_new n USING (concept_id)")
    cur.execute("SELECT vocabulary_id, count(*), count(*) FILTER (WHERE standard_concept = 'S') FROM _s_new GROUP BY 1")
    for vocabulary_id, n, standard in cur.fetchall():
        counts[f"supplement:{vocabulary_id}"] = n
        log(f"  + {vocabulary_id}: 개념 {n:,}개 보충")
        if standard:
            log(f"    ! 표준 개념 {standard:,}개는 concept_ancestor가 없어 계층 조회에서 빠집니다")
    if "concept_relationship" in files:
        cur.execute(
            f"INSERT INTO {schema}.concept_relationship SELECT r.* FROM _s_concept_relationship r "
            "WHERE r.concept_id_1 IN (SELECT concept_id FROM _s_new) OR r.concept_id_2 IN (SELECT concept_id FROM _s_new)"
        )
        log(f"  + 관계 {cur.rowcount:,}건 보충")
    if "concept_synonym" in files:
        cur.execute(
            f"INSERT INTO {schema}.concept_synonym SELECT s.* FROM _s_concept_synonym s JOIN _s_new n USING (concept_id)"
        )
    cur.execute("DROP TABLE _s_new, " + ", ".join(f"_s_{t}" for t in staged))
    return counts


def load_vocab(conn, vocab_dir: Path, schema: str, extra_dirs=(), log=print) -> dict[str, int]:
    """한 트랜잭션으로 전부 교체한다. 중간에 실패하면 이전 어휘가 그대로 남는다.

    extra_dirs: 같은 릴리스로 따로 받은 묶음. 기본 묶음에 없는 어휘만 골라 덧붙인다.
    """
    schema = validate_schema(schema)
    files = find_vocab_files(vocab_dir)
    missing = [t for t in REQUIRED_TABLES if t not in files]
    if missing:
        raise FileNotFoundError(f"{vocab_dir}에 필수 Athena 파일이 없습니다: {', '.join(missing)}")
    plans = [(d, plan_supplement(vocab_dir, d)) for d in extra_dirs]
    for table in ATHENA_TABLES:
        if table not in files:
            log(f"  - {table}: 파일 없음, 건너뜀")

    counts: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table, path in files.items():
                cur.execute(f"TRUNCATE {schema}.{table}")
                counts[table] = _copy_file(cur, path, f"{schema}.{table}")
                log(f"  {table}: {counts[table]:,}행")
            for extra_dir, vocabulary_ids in plans:
                if not vocabulary_ids:
                    log(f"  = {extra_dir.name}: 보충할 어휘 없음")
                    continue
                counts.update(_supplement(cur, extra_dir, schema, vocabulary_ids, log))
        if ensure_indexes(conn, schema):
            log("  PK·인덱스 생성 완료")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts
