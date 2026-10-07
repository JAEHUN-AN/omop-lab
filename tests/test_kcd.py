from omoplab.etl.concepts import ConceptMatch
from omoplab.kcd import KcdVersions, parse_new_to_old, to_kcd7
from omoplab.measure import classify, summarize

# 실제 연계표 내용은 쓰지 않는다 (통계법 제22조) — 형식만 흉내 낸 가짜 표
HEADER = [("분류기준", "9차 코드", "한글명칭", "영문명칭", "8차코드")]


def test_parse_new_to_old_splits_lists_and_marks_new_codes():
    table = parse_new_to_old(HEADER + [
        ("세세", "X01.10", "가", "a", "X01.1"),
        ("세세", "X01.20", "나", "b", "X01.2, X01.3"),
        ("세세", "X01.28", "다", "c", "-"),
        ("세", None, None, None, None),
    ])

    assert table == {"X01.10": ("X01.1",), "X01.20": ("X01.2", "X01.3"), "X01.28": ()}


def test_to_kcd7_walks_back_through_every_revision():
    versions = KcdVersions(
        back=({"X01.10": ("U18.0",), "X01.28": ()}, {"U18.0": ("U19",), "Y02.1": ("Y02.0",)}),
        current_codes=frozenset({"X01.10", "X01.28", "E11.9"}),
        sources=("가짜9", "가짜8"),
    )

    assert to_kcd7("X01.10", versions) == ("U19",)
    assert to_kcd7("E11.9", versions) == ("E11.9",)  # 바뀌지 않은 코드는 그대로
    assert to_kcd7("X01.28", versions) == ()  # 옛 대응이 없는 신설 코드
    assert to_kcd7("Y02.1", versions) == ("Y02.0",)  # 8차 코드를 그대로 쓰는 병원


def test_classify_uses_crosswalk_before_parent_and_flags_current_only_codes():
    versions = KcdVersions(
        back=({"B34.20": ("U18.0",), "B34.28": ()}, {"U18.0": ("U19",)}),
        current_codes=frozenset({"B34.20", "B34.28"}),
        sources=("가짜9", "가짜8"),
    )
    lookup = {
        ("KCD", "U19"): ConceptMatch(1, 4000001, "Condition"),  # MERS
        ("KCD", "B34.2"): ConceptMatch(2, 4000002, "Condition"),  # 코로나바이러스 감염, 상세불명
    }

    mers = classify({"code": "B3420"}, "KCD", lookup, versions)
    new = classify({"code": "B34.28"}, "KCD", lookup, versions)
    junk = classify({"code": "Q99.9"}, "KCD", lookup, versions)

    # 상위 코드(B34.2)로 뭉개지 않고 연계표를 따라 MERS로 간다
    assert (mers.match_level, mers.matched_code, mers.standard_concept_id) == ("crosswalk", "U19", 4000001)
    # 옛 대응이 없으면 상위 코드로 내려간다
    assert (new.match_level, new.matched_code, new.current_only) == ("parent", "B34.2", True)
    assert (junk.match_level, junk.current_only) == ("none", False)

    (s,) = summarize([mers, new, junk])
    assert (s.crosswalk, s.parent, s.missing, s.current_only) == (1, 1, 1, 1)
