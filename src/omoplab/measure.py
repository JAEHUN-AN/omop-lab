"""병원 코드 목록 → 국내 표준 어휘(KCD7·EDI) → OMOP 표준 개념 연결률 측정.

입력 CSV 열:
  code   (필수) 원내 코드
  system (선택) KCD 또는 EDI. 없으면 --system 값을 쓴다
  group  (선택) 병원 등 비교 단위
  count  (선택) 사용량(환자수·건수). 있으면 사용량 가중 연결률을 함께 낸다
  name   (선택) 코드명. 결과 CSV에는 옮기지만 화면에는 --show-names일 때만 찍는다

판정:
  1. 정확 일치 — 정규화한 코드가 어휘에 있다
  2. 상위 일치 — KCD만. 형식이 맞는 코드에서 소수점 아래를 한 자리씩 떼어 3자리 분류까지 올라간다
     (EDI는 접두사를 자르면 약제가 검사 코드가 되는 식으로 다른 분류에 붙어서 하지 않는다)
  3. 미발견
"표준 연결"은 찾은 국내 코드에 유효한 표준 개념 'Maps to'가 있는 경우다. 폐기된 국내 코드도 따로 센다.
"""

import csv
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from omoplab.ddl import validate_schema
from omoplab.etl.concepts import SYSTEM_VOCABULARIES, UNMAPPED, ConceptLookup, lookup_concepts
from omoplab.kcd import KcdVersions, to_kcd7

SYSTEMS = ("KCD", "EDI")
DEFAULT_GROUP = "(전체)"
_FORMATS = {
    "KCD": re.compile(r"[A-Z]\d{2}(\.\d{1,3})?", re.ASCII),
    "EDI": re.compile(r"[A-Z0-9]{5,9}", re.ASCII),
}
# KCD 앞 3자리 분류 + 원내 확장 자릿수(숫자·영문). 예: E11.9A, E11.900123 — 상위 탐색을 허용한다
_KCD_EXTENDED = re.compile(r"[A-Z]\d{2}\.[0-9A-Z]{1,12}", re.ASCII)
# KCD 표기에 붙는 검표(†, 병인)·별표(*, 발현) — 코드 자체가 아니다
_KCD_MARKS = re.compile(r"[†*]")
_ENCODINGS = ("utf-8-sig", "cp949")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


class CodeResult(NamedTuple):
    group: str
    system: str
    raw_code: str
    code: str
    name: str
    weight: float | None  # count 열이 없으면 None
    valid_format: bool
    extended: bool  # 형식은 KCD가 아니지만 KCD + 원내 확장 자릿수로 읽히는 코드
    match_level: str  # exact | crosswalk | parent | none
    matched_code: str
    current_only: bool  # KCD7로 바로·연계로 못 가지만 최신 KCD(9차)에는 있는 코드
    deprecated: bool  # 찾은 국내 코드가 폐기·대체된 코드인가
    source_concept_id: int
    standard_concept_id: int
    standard_domain: str


class Summary(NamedTuple):
    group: str
    system: str
    codes: int
    invalid: int
    extended: int
    exact: int
    crosswalk: int
    parent: int
    missing: int
    current_only: int
    deprecated: int
    mapped_exact: int
    mapped_parent: int
    mapped_pct: float
    weighted_mapped_pct: float | None
    domains: dict[str, int]


def normalize(system: str, raw: str) -> str:
    code = re.sub(r"\s+", "", raw).upper()
    if system == "KCD":
        bare = _KCD_MARKS.sub("", code).replace(".", "")
        return f"{bare[:3]}.{bare[3:]}" if len(bare) > 3 else bare
    return code


def is_valid_format(system: str, code: str) -> bool:
    return _FORMATS[system].fullmatch(code) is not None


def candidate_codes(system: str, code: str) -> list[str]:
    """찾아볼 코드 목록. KCD는 자기 자신부터 3자리 분류까지, EDI는 자기 자신만."""
    candidates = [code]
    if system == "KCD":
        while "." in code:
            code = code[:-1].rstrip(".")
            candidates.append(code)
    return candidates


def _find(system: str, candidates: Sequence[str], lookup: ConceptLookup):
    for i, candidate in enumerate(candidates):
        if (system, candidate) in lookup:
            return i, candidate, lookup[(system, candidate)]
    return None


def classify(row: Mapping[str, str], system: str, lookup: ConceptLookup,
             versions: KcdVersions | None = None) -> CodeResult:
    """정확 일치 → (KCD) 개정 연계표로 KCD7 되돌리기 → (KCD) 상위 코드 → 미발견."""
    raw = row["code"]
    code = normalize(system, raw)
    valid = is_valid_format(system, code)
    extended = not valid and system == "KCD" and _KCD_EXTENDED.fullmatch(code) is not None
    level, matched, match = "none", "", UNMAPPED

    exact = _find(system, [code], lookup)
    # 연계표는 상위 탐색보다 먼저 본다 — 예: KCD-9 B34.20(MERS)은 상위 B34.2가 아니라 KCD7 MERS 코드로 가야 한다
    crosswalk = None
    if exact is None and versions is not None and system == "KCD" and valid:
        crosswalk = _find(system, [c for c in to_kcd7(code, versions) if c != code], lookup)
    # 깨진 코드는 어디까지가 표준 코드인지 알 수 없으므로 상위 탐색을 하지 않는다
    parent = None
    if exact is None and crosswalk is None and (valid or extended):
        parent = _find(system, candidate_codes(system, code)[1:], lookup)

    if exact is not None:
        level, matched, match = "exact", exact[1], exact[2]
    elif crosswalk is not None:
        level, matched, match = "crosswalk", crosswalk[1], crosswalk[2]
    elif parent is not None:
        level, matched, match = "parent", parent[1], parent[2]

    count = row.get("count")
    weight = None if count is None else float(count or 0)  # count 열은 있는데 빈 칸이면 사용량 0
    current_only = (versions is not None and system == "KCD" and level in ("parent", "none")
                    and code in versions.current_codes)
    return CodeResult(
        group=row.get("group") or DEFAULT_GROUP,
        system=system,
        raw_code=raw,
        code=code,
        name=row.get("name", ""),
        weight=weight,
        valid_format=valid,
        extended=extended,
        match_level=level,
        matched_code=matched,
        current_only=current_only,
        deprecated=match.source_invalid is not None,
        source_concept_id=match.source_concept_id,
        standard_concept_id=match.standard_concept_id,
        standard_domain=match.domain_id or "",
    )


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _dedupe(results: Iterable[CodeResult]) -> dict[tuple[str, str, str], tuple[CodeResult, float | None]]:
    """(group, system, code)별 첫 판정과 합친 사용량."""
    merged: dict[tuple[str, str, str], tuple[CodeResult, float | None]] = {}
    for r in results:
        key = (r.group, r.system, r.code)
        first, weight = merged.get(key, (r, None))
        if r.weight is not None:
            weight = (weight or 0.0) + r.weight
        merged[key] = (first, weight)
    return merged


def summarize(results: Iterable[CodeResult]) -> list[Summary]:
    """(group, system)별로 집계한다. 같은 코드가 여러 번 나오면 1개로 세고 사용량은 합친다."""
    by_key: dict[tuple[str, str], list[tuple[CodeResult, float | None]]] = {}
    for (group, system, _), item in _dedupe(results).items():
        by_key.setdefault((group, system), []).append(item)

    summaries = []
    for (group, system), items in sorted(by_key.items()):
        rows = [r for r, _ in items]
        levels = Counter(r.match_level for r in rows)
        mapped = [r for r in rows if r.standard_concept_id]
        weighted = any(w is not None for _, w in items)
        total_weight = sum(w or 0.0 for _, w in items)
        mapped_weight = sum(w or 0.0 for r, w in items if r.standard_concept_id)
        summaries.append(
            Summary(
                group=group,
                system=system,
                codes=len(rows),
                invalid=sum(not r.valid_format and not r.extended for r in rows),
                extended=sum(r.extended for r in rows),
                exact=levels["exact"],
                crosswalk=levels["crosswalk"],
                parent=levels["parent"],
                missing=levels["none"],
                current_only=sum(r.current_only for r in rows),
                deprecated=sum(r.deprecated for r in rows),
                mapped_exact=sum(r.match_level in ("exact", "crosswalk") for r in mapped),
                mapped_parent=sum(r.match_level == "parent" for r in mapped),
                mapped_pct=_pct(len(mapped), len(rows)),
                weighted_mapped_pct=_pct(mapped_weight, total_weight) if weighted and total_weight else None,
                domains=dict(Counter(r.standard_domain for r in mapped)),
            )
        )
    return summaries


def _parse_count(value: str, where: str) -> str:
    if value == "":
        return value
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        raise ValueError(f"{where}: count가 숫자가 아닙니다: {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{where}: count는 0 이상의 유한한 숫자여야 합니다: {value!r}")
    return str(number)


def read_code_list(path: Path) -> list[dict[str, str]]:
    """엑셀에서 저장한 CSV는 UTF-8(BOM)이나 CP949라 둘 다 받는다. 열 이름은 소문자로 맞춘다."""
    for encoding in _ENCODINGS:
        try:
            with path.open(encoding=encoding, newline="") as f:
                rows = [{k.strip().lower(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path}: UTF-8도 CP949도 아닙니다")
    if rows and "code" not in rows[0]:
        raise ValueError(f"{path}: 'code' 열이 필요합니다 (있는 열: {', '.join(rows[0])})")
    parsed = []
    for line, row in enumerate(rows, start=2):  # 1행은 헤더
        if not row.get("code"):
            continue
        if "count" in row:
            row = {**row, "count": _parse_count(row["count"], f"{path}:{line}")}
        parsed.append(row)
    return parsed


def vocabulary_status(conn, schema: str, systems: Iterable[str]) -> dict[str, str]:
    """측정에 필요한 어휘가 적재돼 있는지 확인하고 버전을 돌려준다. 없으면 실패시킨다.

    어휘가 없으면 모든 코드가 '미발견'으로 나와 "한국 고유 코드라 짝이 없다"와 구분할 수 없기 때문이다.
    """
    schema = validate_schema(schema)
    versions: dict[str, str] = {}
    with conn.cursor() as cur:
        for system in systems:
            for vocabulary_id in SYSTEM_VOCABULARIES[system]:
                cur.execute(f"SELECT vocabulary_version FROM {schema}.vocabulary WHERE vocabulary_id = %s", (vocabulary_id,))
                row = cur.fetchone()
                cur.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {schema}.concept c JOIN {schema}.concept_relationship r "
                    f"ON r.concept_id_1 = c.concept_id AND r.relationship_id = 'Maps to' WHERE c.vocabulary_id = %s)",
                    (vocabulary_id,),
                )
                has_maps = cur.fetchone()[0]
                if row is None or not has_maps:
                    raise ValueError(f"{vocabulary_id} 어휘(또는 그 'Maps to' 관계)가 적재돼 있지 않습니다. load-vocab을 먼저 하세요")
                versions[vocabulary_id] = row[0]
    return versions


def measure(conn, schema: str, rows: Sequence[Mapping[str, str]], default_system: str | None,
            versions: KcdVersions | None = None) -> list[CodeResult]:
    systems = []
    for row in rows:
        system = (row.get("system") or default_system or "").upper()
        if system not in SYSTEMS:
            raise ValueError(f"코드 {row['code']!r}의 체계를 알 수 없습니다. system 열이나 --system에 KCD/EDI를 주세요")
        systems.append(system)
    keys: set[tuple[str, str]] = set()
    for row, system in zip(rows, systems):
        code = normalize(system, row["code"])
        keys.update((system, c) for c in candidate_codes(system, code))
        if versions is not None and system == "KCD":
            keys.update((system, c) for c in to_kcd7(code, versions))
    lookup = lookup_concepts(conn, schema, keys)
    return [classify(row, system, lookup, versions) for row, system in zip(rows, systems)]


def _safe_cell(value):
    # 엑셀이 '='·'+'·'-'·'@'로 시작하는 값을 수식으로 실행하지 않게 한다
    return f"'{value}" if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES) else value


def write_results(path: Path, results: Iterable[CodeResult]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CodeResult._fields)
        writer.writerows([_safe_cell(v) for v in r] for r in results)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cell(value, width: int, left: bool = False) -> str:
    """한글은 터미널에서 두 칸을 차지하므로 글자 수가 아니라 화면 폭으로 맞춘다."""
    text = "-" if value is None else str(value)
    pad = " " * max(width - _display_width(text), 0)
    return text + pad if left else pad + text


_COLUMNS = (("group", 14, True), ("체계", 6, True), ("코드", 8, False), ("형식오류", 9, False), ("원내확장", 9, False),
            ("어휘정확", 9, False), ("개정연계", 9, False), ("어휘상위", 9, False), ("미발견", 8, False),
            ("최신KCD만", 10, False), ("폐기", 7, False),
            ("연결(정확·연계)", 16, False), ("연결(상위)", 11, False), ("연결%", 8, False), ("가중%", 8, False))

LEGEND = (
    "형식오류: 표준 코드 형식이 아니고 원내 확장으로도 읽히지 않는 코드 / 원내확장: KCD + 병원 고유 자릿수 (상위 코드로 찾음)\n"
    "어휘정확/개정연계/어휘상위/미발견: 국내 어휘(KCD7·EDI)에서 찾은 방식 (합 = 코드). 개정연계 = KCD 9→8→7 연계표로 찾음\n"
    "최신KCD만: KCD7로는 바로도 연계로도 못 가지만 최신 KCD(9차)에는 있는 코드 — 깨진 코드가 아니라 어휘 버전 공백\n"
    "폐기: 찾았지만 폐기·대체된 국내 코드 / 연결: 유효한 표준 개념까지 이어진 코드 (연결% = 연결 합 / 코드)\n"
    "가중%: count(사용량) 기준 연결 비율 — count 열이 없으면 '-'"
)


def format_report(summaries: Sequence[Summary], results: Sequence[CodeResult], top: int,
                  show_names: bool = False, versions: Mapping[str, str] | None = None) -> str:
    lines = []
    if versions:
        lines.append("어휘: " + ", ".join(f"{k} {v}" for k, v in versions.items()))
    lines.append("".join(_cell(name, w, left) for name, w, left in _COLUMNS) + "  도메인")
    for s in summaries:
        domains = ", ".join(f"{d} {n}" for d, n in sorted(s.domains.items(), key=lambda x: -x[1]))
        values = (s.group, s.system, s.codes, s.invalid, s.extended, s.exact, s.crosswalk, s.parent, s.missing,
                  s.current_only, s.deprecated,
                  s.mapped_exact, s.mapped_parent, s.mapped_pct, s.weighted_mapped_pct)
        lines.append("".join(_cell(v, w, left) for v, (_, w, left) in zip(values, _COLUMNS)) + f"  {domains}")
    lines.append("")
    lines.append(LEGEND)

    unmapped = [(r, w) for r, w in _dedupe(results).values() if not r.standard_concept_id]
    unmapped.sort(key=lambda item: -(item[1] or 0.0))
    if unmapped and top:
        lines.append(f"\n표준 개념이 없는 코드 (사용량 상위 {top}):")
        for r, weight in unmapped[:top]:
            line = ("  " + _cell(r.group, 14, True) + _cell(r.system, 5, True) + _cell(r.code, 18, True)
                    + _cell(r.match_level, 7, True) + _cell("-" if weight is None else f"{weight:g}", 12))
            lines.append(line + (f"  {r.name}" if show_names and r.name else ""))
    return "\n".join(lines)
