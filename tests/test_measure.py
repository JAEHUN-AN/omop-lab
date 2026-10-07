import unicodedata

import pytest

from omoplab.etl.concepts import ConceptMatch
from omoplab.measure import (
    candidate_codes,
    classify,
    format_report,
    is_valid_format,
    normalize,
    read_code_list,
    summarize,
    write_results,
)

E119 = ConceptMatch(1572001, 201826, "Condition")
E11 = ConceptMatch(1572000, 201820, "Condition")
U19 = ConceptMatch(1599000, 0, None)  # KCD7에는 있지만 표준 개념이 없는 한국 고유 코드
OLD_EDI = ConceptMatch(2000001, 3000001, "Procedure", "D")  # 폐기된 EDI 코드


@pytest.mark.parametrize(
    "system, raw, expected",
    [
        ("KCD", "e119", "E11.9"),
        ("KCD", " E11.9 ", "E11.9"),
        ("KCD", "E11", "E11"),
        ("KCD", "S25828", "S25.828"),
        ("KCD", "I21.0†", "I21.0"),  # 병원에서 흔한 검표·별표 표기
        ("KCD", "G01*", "G01"),
        ("EDI", " d5821080 ", "D5821080"),
    ],
)
def test_normalize(system, raw, expected):
    assert normalize(system, raw) == expected


@pytest.mark.parametrize(
    "system, code, valid",
    [("KCD", "E11.9", True), ("KCD", "E11", True), ("KCD", "E11.9001", False), ("KCD", "11.9", False),
     ("KCD", "E１1", False), ("EDI", "D5821080", True), ("EDI", "AA100", True), ("EDI", "B25", False),
     ("EDI", "B2570-01", False)],
)
def test_is_valid_format(system, code, valid):
    assert is_valid_format(system, code) is valid


def test_candidate_codes_walk_up_kcd_but_not_edi():
    assert candidate_codes("KCD", "E11.95") == ["E11.95", "E11.9", "E11"]
    assert candidate_codes("KCD", "E11") == ["E11"]
    # EDI는 접두사를 자르면 다른 분류(약제 → 검사 등)의 코드가 되므로 상위 탐색을 하지 않는다
    assert candidate_codes("EDI", "B10500019") == ["B10500019"]


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
    assert exact.weight is None  # count 열이 없으면 가중치도 없다


@pytest.mark.parametrize("code", ["0353.000.80", "G]8", "E1"])
def test_classify_does_not_walk_up_from_broken_code(code):
    result = classify({"code": code}, "KCD", {("KCD", "E11.9"): E119, ("KCD", "E11"): E11})

    assert (result.valid_format, result.extended, result.match_level) == (False, False, "none")


@pytest.mark.parametrize("code, matched", [("E11.9001", "E11.9"), ("E11.9A", "E11.9"), ("E11.900123", "E11.9")])
def test_classify_walks_up_from_hospital_extended_kcd(code, matched):
    result = classify({"code": code}, "KCD", {("KCD", "E11.9"): E119, ("KCD", "E11"): E11})

    assert (result.valid_format, result.extended, result.match_level, result.matched_code) == (False, True, "parent", matched)


def test_classify_marks_deprecated_source_codes():
    result = classify({"code": "A0000001"}, "EDI", {("EDI", "A0000001"): OLD_EDI})

    assert result.deprecated is True and result.standard_concept_id == 3000001


def test_summarize_reports_rates_per_group_and_weighted_coverage():
    lookup = {("KCD", "E11.9"): E119, ("KCD", "E11"): E11, ("KCD", "U19"): U19}
    rows = [
        {"group": "A", "code": "E119", "count": "90"},
        {"group": "A", "code": "U19", "count": "10"},
        {"group": "A", "code": "E119", "count": "5"},  # 같은 코드 중복 → 1개로 세고 가중치는 합친다
        {"group": "A", "code": "E11.98", "count": "0"},
        {"group": "B", "code": "ZZZ", "count": "1"},
    ]

    summary = {s.group: s for s in summarize([classify(r, "KCD", lookup) for r in rows])}

    a = summary["A"]
    assert (a.codes, a.exact, a.parent, a.missing) == (3, 2, 1, 0)
    assert (a.mapped_exact, a.mapped_parent) == (1, 1)
    assert a.mapped_pct == round(2 / 3 * 100, 1)
    assert a.weighted_mapped_pct == round(95 / 105 * 100, 1)
    assert a.domains == {"Condition": 2}
    assert summary["B"].invalid == 1 and summary["B"].mapped_pct == 0.0
    assert a.extended == 0


def test_summarize_without_counts_has_no_weighted_rate():
    (s,) = summarize([classify({"code": "E119"}, "KCD", {("KCD", "E11.9"): E119})])

    assert s.weighted_mapped_pct is None


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


@pytest.mark.parametrize("bad", ["abc", "-3", "nan", "inf"])
def test_read_code_list_rejects_bad_counts_with_line_number(tmp_path, bad):
    path = tmp_path / "c.csv"
    path.write_text(f"code,count\nE119,1\nE110,{bad}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=":3"):
        read_code_list(path)


def test_report_hides_names_unless_asked():
    results = [classify({"group": "B", "code": "U19", "name": "메르스", "count": "3"}, "KCD", {("KCD", "U19"): U19})]
    summaries = summarize(results)

    assert "메르스" not in format_report(summaries, results, top=5)
    assert "메르스" in format_report(summaries, results, top=5, show_names=True)


def test_report_ranks_unmapped_codes_by_summed_weight():
    rows = [{"group": "A", "code": "S25.828", "count": "5"}, {"group": "A", "code": "S25.828", "count": "1"},
            {"group": "A", "code": "T99.9", "count": "5.5"}]
    results = [classify(r, "KCD", {}) for r in rows]

    lines = format_report(summarize(results), results, top=5).splitlines()
    unmapped = [line for line in lines if "S25.828" in line or "T99.9" in line]

    assert "S25.828" in unmapped[0] and unmapped[0].rstrip().endswith("6")


def test_format_report_aligns_korean_by_display_width():
    lookup = {("KCD", "E11.9"): E119}
    results = [classify({"group": "병원A", "code": "E119"}, "KCD", lookup),
               classify({"group": "B", "code": "U19"}, "KCD", lookup)]

    report = format_report(summarize(results), results, top=5)

    header, row_b, row_hospital = [line for line in report.splitlines() if line][:3]  # 그룹은 이름순: "B" < "병원A"
    widths = {
        _width(header.removesuffix("  도메인")),
        _width(row_b.removesuffix("  ")),
        _width(row_hospital.removesuffix("  Condition 1")),
    }
    assert len(widths) == 1


def test_write_results_neutralizes_spreadsheet_formulas(tmp_path):
    out = tmp_path / "r.csv"
    write_results(out, [classify({"code": "E119", "name": "=HYPERLINK(1)"}, "KCD", {})])

    assert "'=HYPERLINK(1)" in out.read_text(encoding="utf-8-sig")


def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def test_summarize_zero_usage_has_no_weighted_rate():
    (s,) = summarize([classify({"code": "E119", "count": "0"}, "KCD", {("KCD", "E11.9"): E119})])

    assert s.weighted_mapped_pct is None
