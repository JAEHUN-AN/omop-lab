"""KCD 개정 연계 — 최신 KCD(8·9차) 코드를 Athena에 있는 KCD7 코드로 되돌린다.

Athena에는 KCD7만 있다. 국내 의료기관은 KCD-9(2025-07 고시)나 KCD-8을 쓰므로, 7차 이후 바뀐 코드는
그대로는 찾을 수 없다. 국가데이터처 통계분류포털의 신구 연계표(새 코드 → 옛 코드)를 9→8→7 순서로 따라간다.

파일은 통계분류포털 > 한국표준질병사인분류 > 자료실 > 최신개정에서 받아 vocab/kcd/ 에 둔다 (gitignore).
통계법 제22조에 따라 연계표 원본·가공본은 이 리포에 싣지 않는다.
"""

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

NEW_TO_OLD_SHEET = "신구연계표"
MASTER_SHEET = "KCD-9 DB Masterfile"
# 최신 → 옛 순서로 따라간다
CROSSWALK_GLOBS = ("kcd9_crosswalk*.xlsx", "kcd8_crosswalk*.xlsx")
MASTERFILE_GLOB = "kcd9_masterfile*.xlsx"
_CODE = re.compile(r"[A-Z]\d{2}(\.\d{1,3})?", re.ASCII)


class KcdVersions(NamedTuple):
    back: tuple[dict[str, tuple[str, ...]], ...]  # 최신 → 옛 순서의 신구 연계표들
    current_codes: frozenset[str]  # 최신 KCD(9차)의 모든 코드
    sources: tuple[str, ...]  # 읽은 파일 이름 (보고서 표시용)


def _norm(value) -> str:
    return re.sub(r"[\s†*+]", "", str(value)).upper()


def parse_new_to_old(rows: Iterable[Sequence]) -> dict[str, tuple[str, ...]]:
    """신구 연계표 행 → {새 코드: (옛 코드, ...)}. 옛 코드가 '-'면 대응 없는 신설 코드라 빈 튜플."""
    table: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if len(row) < 5 or row[1] is None:
            continue
        new = _norm(row[1])
        if not _CODE.fullmatch(new):  # 머리글·안내 행
            continue
        olds = str(row[4] or "").strip()
        table[new] = () if olds in ("", "-") else tuple(o for o in (_norm(x) for x in olds.split(",")) if o)
    return table


def to_kcd7(code: str, versions: KcdVersions) -> tuple[str, ...]:
    """KCD7 후보 코드들. 연계표에 없는 코드는 그 차수에서 바뀌지 않은 것으로 보고 그대로 넘긴다."""
    current = (code,)
    for table in versions.back:
        stepped: list[str] = []
        for c in current:
            stepped.extend(table.get(c, (c,)))
        current = tuple(dict.fromkeys(stepped))
    return current


def _sheet_rows(path: Path, sheet: str):
    import openpyxl  # 측정 때만 필요하므로 지연 로딩

    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        return list(workbook[sheet].iter_rows(values_only=True))
    finally:
        workbook.close()


def _latest(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern))
    return found[-1] if found else None


def load_kcd_versions(directory: Path) -> KcdVersions | None:
    """vocab/kcd/ 의 연계표·Masterfile을 읽는다. 연계표가 하나도 없으면 None (연계 없이 측정)."""
    crosswalks = [p for p in (_latest(directory, g) for g in CROSSWALK_GLOBS) if p is not None]
    if not crosswalks:
        return None
    back = tuple(parse_new_to_old(_sheet_rows(p, NEW_TO_OLD_SHEET)) for p in crosswalks)
    master = _latest(directory, MASTERFILE_GLOB)
    codes: frozenset[str] = frozenset()
    if master is not None:
        codes = frozenset(
            code for code in (_norm(r[2]) for r in _sheet_rows(master, MASTER_SHEET) if len(r) > 2 and r[2])
            if _CODE.fullmatch(code)
        )
    sources = tuple(p.name for p in crosswalks) + ((master.name,) if master else ())
    return KcdVersions(back=back, current_codes=codes, sources=sources)
