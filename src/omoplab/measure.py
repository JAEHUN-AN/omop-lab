"""병원 코드 목록 → 국내 표준 어휘(KCD7·EDI) → OMOP 표준 개념 연결률 측정.

입력 CSV 열:
  code   (필수) 원내 코드
  system (선택) KCD 또는 EDI. 없으면 --system 값을 쓴다
  group  (선택) 병원 등 비교 단위
  count  (선택) 사용량(환자수·건수). 있으면 사용량 가중 연결률을 함께 낸다
  name   (선택) 코드명. 결과 CSV에 그대로 옮긴다

판정은 정확 일치 → 상위 코드 일치(원내 확장 자릿수를 떼어 냄) → 미발견 순서다.
"""

import csv
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from omoplab.etl.concepts import UNMAPPED, ConceptLookup, lookup_concepts

SYSTEMS = ("KCD", "EDI")
DEFAULT_GROUP = "(전체)"
_FORMATS = {
    "KCD": re.compile(r"[A-Z]\d{2}(\.\d{1,3})?"),
    "EDI": re.compile(r"[A-Z0-9]{5,9}"),
}
_EDI_MIN_LENGTH = 5  # EDI 상위 분류(Proc Hierarchy·Meas Class)의 최소 길이
_ENCODINGS = ("utf-8-sig", "cp949")


class CodeResult(NamedTuple):
    group: str
    system: str
    raw_code: str
    code: str
    name: str
    weight: float
    valid_format: bool
    match_level: str  # exact | parent | none
    matched_code: str
    source_concept_id: int
    standard_concept_id: int
    standard_domain: str


class Summary(NamedTuple):
    group: str
    system: str
    codes: int
    invalid: int
    exact: int
    parent: int
    missing: int
    mapped: int
    mapped_pct: float
    weighted_mapped_pct: float
    domains: dict[str, int]


def normalize(system: str, raw: str) -> str:
    code = re.sub(r"\s+", "", raw).upper()
    if system == "KCD":
        bare = code.replace(".", "")
        return f"{bare[:3]}.{bare[3:]}" if len(bare) > 3 else bare
    return code


def is_valid_format(system: str, code: str) -> bool:
    return _FORMATS[system].fullmatch(code) is not None


def candidate_codes(system: str, code: str) -> list[str]:
    """자기 자신부터 한 자리씩 떼어 낸 상위 코드까지."""
    if system == "KCD":
        candidates = [code]
        while "." in code:
            code = code[:-1].rstrip(".")
            candidates.append(code)
        return candidates
    return [code[:n] for n in range(len(code), _EDI_MIN_LENGTH - 1, -1)] or [code]


def _weight(row: Mapping[str, str]) -> float:
    value = (row.get("count") or "").replace(",", "").strip()
    return float(value) if value else 1.0


def classify(row: Mapping[str, str], system: str, lookup: ConceptLookup) -> CodeResult:
    raw = row["code"]
    code = normalize(system, raw)
    level, matched, match = "none", "", UNMAPPED
    for i, candidate in enumerate(candidate_codes(system, code)):
        if (system, candidate) in lookup:
            level, matched, match = ("exact" if i == 0 else "parent"), candidate, lookup[(system, candidate)]
            break
    return CodeResult(
        group=row.get("group") or DEFAULT_GROUP,
        system=system,
        raw_code=raw,
        code=code,
        name=row.get("name", ""),
        weight=_weight(row),
        valid_format=is_valid_format(system, code),
        match_level=level,
        matched_code=matched,
        source_concept_id=match.source_concept_id,
        standard_concept_id=match.standard_concept_id,
        standard_domain=match.domain_id or "",
    )


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def summarize(results: Iterable[CodeResult]) -> list[Summary]:
    """(group, system)별로 집계한다. 같은 코드가 여러 번 나오면 1개로 세고 사용량은 합친다."""
    by_key: dict[tuple[str, str], dict[str, CodeResult]] = {}
    weights: dict[tuple[str, str, str], float] = {}
    for r in results:
        by_key.setdefault((r.group, r.system), {}).setdefault(r.code, r)
        weight_key = (r.group, r.system, r.code)
        weights[weight_key] = weights.get(weight_key, 0.0) + r.weight

    summaries = []
    for (group, system), codes in sorted(by_key.items()):
        rows = list(codes.values())
        levels = Counter(r.match_level for r in rows)
        mapped = [r for r in rows if r.standard_concept_id]
        total_weight = sum(weights[(group, system, r.code)] for r in rows)
        mapped_weight = sum(weights[(group, system, r.code)] for r in mapped)
        summaries.append(
            Summary(
                group=group,
                system=system,
                codes=len(rows),
                invalid=sum(not r.valid_format for r in rows),
                exact=levels["exact"],
                parent=levels["parent"],
                missing=levels["none"],
                mapped=len(mapped),
                mapped_pct=_pct(len(mapped), len(rows)),
                weighted_mapped_pct=_pct(mapped_weight, total_weight),
                domains=dict(Counter(r.standard_domain for r in mapped)),
            )
        )
    return summaries


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
    return [r for r in rows if r.get("code")]


def measure(conn, schema: str, rows: Sequence[Mapping[str, str]], default_system: str | None) -> list[CodeResult]:
    systems = []
    for row in rows:
        system = (row.get("system") or default_system or "").upper()
        if system not in SYSTEMS:
            raise ValueError(f"코드 {row['code']!r}의 체계를 알 수 없습니다. system 열이나 --system에 KCD/EDI를 주세요")
        systems.append(system)
    keys = {
        (system, candidate)
        for row, system in zip(rows, systems)
        for candidate in candidate_codes(system, normalize(system, row["code"]))
    }
    lookup = lookup_concepts(conn, schema, keys)
    return [classify(row, system, lookup) for row, system in zip(rows, systems)]


def write_results(path: Path, results: Iterable[CodeResult]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CodeResult._fields)
        writer.writerows(results)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cell(value, width: int, left: bool = False) -> str:
    """한글은 터미널에서 두 칸을 차지하므로 글자 수가 아니라 화면 폭으로 맞춘다."""
    text = str(value)
    pad = " " * max(width - _display_width(text), 0)
    return text + pad if left else pad + text


_COLUMNS = (("group", 14, True), ("체계", 6, True), ("코드", 7, False), ("형식오류", 9, False),
            ("정확", 7, False), ("상위", 7, False), ("미발견", 8, False), ("표준연결", 9, False),
            ("연결%", 8, False), ("가중%", 8, False))


def format_report(summaries: Sequence[Summary], results: Sequence[CodeResult], top: int) -> str:
    lines = ["".join(_cell(name, w, left) for name, w, left in _COLUMNS) + "  도메인"]
    for s in summaries:
        domains = ", ".join(f"{d} {n}" for d, n in sorted(s.domains.items(), key=lambda x: -x[1]))
        values = (s.group, s.system, s.codes, s.invalid, s.exact, s.parent, s.missing,
                  s.mapped, s.mapped_pct, s.weighted_mapped_pct)
        lines.append("".join(_cell(v, w, left) for v, (_, w, left) in zip(values, _COLUMNS)) + f"  {domains}")
    unmapped = sorted((r for r in results if not r.standard_concept_id), key=lambda r: -r.weight)
    if unmapped and top:
        lines.append(f"\n표준 개념이 없는 코드 (사용량 상위 {top}):")
        seen: set[tuple[str, str, str]] = set()
        for r in unmapped:
            key = (r.group, r.system, r.code)
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                "  " + _cell(r.group, 14, True) + _cell(r.system, 5, True) + _cell(r.code, 13, True)
                + _cell(r.match_level, 7, True) + _cell(f"{r.weight:g}", 10) + f"  {r.name}"
            )
            if len(seen) >= top:
                break
    return "\n".join(lines)
