import pytest

from omoplab.etl.concepts import ConceptMatch
from omoplab.measure import (
    candidate_codes,
    format_report,
    classify,
    is_valid_format,
    normalize,
    read_code_list,
    summarize,
)

E119 = ConceptMatch(1572001, 201826, "Condition")
E11 = ConceptMatch(1572000, 201820, "Condition")
U19 = ConceptMatch(1599000, 0, None)  # KCD7에는 있지만 표준 개념이 없는 한국 고유 코드


@pytest.mark.parametrize(
    "system, raw, expected",
    [
        ("KCD", "e119", "E11.9"),
        ("KCD", " E11.9 ", "E11.9"),
        ("KCD", "E11", "E11"),
        ("KCD", "S25828", "S25.828"),
        ("EDI", " d5821080 ", "D5821080"),
    ],
)
def test_normalize(system, raw, expected):
    assert normalize(system, raw) == expected


@pytest.mark.parametrize(
    "system, code, valid",
    [("KCD", "E11.9", True), ("KCD", "E11", True), ("KCD", "E11.9001", False), ("KCD", "11.9", False),
     ("EDI", "D5821080", True), ("EDI", "AA100", True), ("EDI", "B25", False), ("EDI", "B2570-01", False)],
)
def test_is_valid_format(system, code, valid):
    assert is_valid_format(system, code) is valid


def test_candidate_codes_walk_up_to_parent_codes():
    assert candidate_codes("KCD", "E11.95") == ["E11.95", "E11.9", "E11"]
    assert candidate_codes("KCD", "E11") == ["E11"]
    assert candidate_codes("EDI", "B2570001") == ["B2570001", "B257000", "B25700", "B2570"]


def test_classify_prefers_exact_then_parent():
    lookup = {("KCD", "E11.9"): E119, ("KCD", "E11"): E11, ("KCD", "U19"): U19}

    exact = classify({"code": "E119", "group": "A"}, "KCD", lookup)
    parent = classify({"code": "E11.98", "group": "A"}, "KCD", lookup)
    korean = classify({"code": "U19", "group": "A"}, "KCD", lookup)
    missing = classify({"code": "Q99.9", "group": "B", "count": "7"}, "KCD", lookup)

    assert (exact.match_level, exact.matched_code, exact.standard_concept_id) == ("exact", "E11.9", 201826)
    assert (parent.match_level, parent.matched_code) == ("parent", "E11.9")
    assert (korean.match_level, korean.standard_concept_id) == ("exact", 0)
    assert (missing.match_level, missing.group, missing.weight) == ("none", "B", 7.0)


def test_summarize_reports_rates_per_group_and_weighted_coverage():
    lookup = {("KCD", "E11.9"): E119, ("KCD", "U19"): U19}
    rows = [
        {"group": "A", "code": "E119", "count": "90"},
        {"group": "A", "code": "U19", "count": "10"},
        {"group": "A", "code": "E119", "count": "5"},  # 같은 코드 중복 → 1개로 세고 가중치는 합친다
        {"group": "B", "code": "ZZZ", "count": "1"},
    ]

    summary = {s.group: s for s in summarize([classify(r, "KCD", lookup) for r in rows])}

    a = summary["A"]
    assert (a.codes, a.exact, a.parent, a.missing, a.mapped) == (2, 2, 0, 0, 1)
    assert a.mapped_pct == 50.0
    assert a.weighted_mapped_pct == round(95 / 105 * 100, 1)
    assert a.domains == {"Condition": 1}
    assert summary["B"].invalid == 1 and summary["B"].mapped_pct == 0.0


def test_read_code_list_accepts_utf8_bom_and_cp949(tmp_path):
    utf8 = tmp_path / "a.csv"
    utf8.write_text("code,group,name\nE119,병원A,당뇨\n", encoding="utf-8-sig")
    cp949 = tmp_path / "b.csv"
    cp949.write_bytes("code,group,name\nE119,병원A,당뇨\n".encode("cp949"))

    assert read_code_list(utf8)[0] == {"code": "E119", "group": "병원A", "name": "당뇨"}
    assert read_code_list(cp949)[0]["group"] == "병원A"


def test_read_code_list_requires_code_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("diag,group\nE119,A\n", encoding="utf-8")

    with pytest.raises(ValueError, match="code"):
        read_code_list(path)


def test_format_report_aligns_korean_by_display_width():
    lookup = {("KCD", "E11.9"): E119}
    results = [classify({"group": "병원A", "code": "E119"}, "KCD", lookup),
               classify({"group": "B", "code": "U19", "name": "메르스"}, "KCD", lookup)]

    report = format_report(summarize(results), results, top=5)

    header, row_b, row_hospital = report.splitlines()[:3]  # 그룹은 이름순: "B" < "병원A"
    widths = {
        _width(header.removesuffix("  도메인")),
        _width(row_b.removesuffix("  ")),
        _width(row_hospital.removesuffix("  Condition 1")),
    }
    assert len(widths) == 1
    assert "U19" in report and "메르스" in report


def _width(text):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
