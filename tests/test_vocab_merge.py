import pytest

from omoplab.vocab import plan_supplement, read_vocabularies

VOCAB_HEADER = "vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\tvocabulary_concept_id\n"


def _bundle(path, rows):
    path.mkdir()
    lines = [f"{vid}\t{vid} name\t\t{version}\t0\n" for vid, version in rows]
    (path / "VOCABULARY.csv").write_text(VOCAB_HEADER + "".join(lines), encoding="utf-8")
    return path


def test_read_vocabularies_returns_versions(tmp_path):
    d = _bundle(tmp_path / "a", [("None", "v5.0 29-AUG-26"), ("KCD7", "7th revision")])

    assert read_vocabularies(d) == {"None": "v5.0 29-AUG-26", "KCD7": "7th revision"}


def test_plan_supplement_picks_vocabularies_missing_from_primary(tmp_path):
    primary = _bundle(tmp_path / "new", [("None", "v5.0 29-AUG-26"), ("SNOMED", "s"), ("EDI", "e")])
    extra = _bundle(tmp_path / "old", [("None", "v5.0 29-AUG-26"), ("SNOMED", "s"), ("KCD7", "k"), ("ICD10", "i")])

    assert plan_supplement(primary, extra) == ("ICD10", "KCD7")


def test_plan_supplement_refuses_different_releases(tmp_path):
    primary = _bundle(tmp_path / "new", [("None", "v5.0 29-AUG-26")])
    extra = _bundle(tmp_path / "old", [("None", "v5.0 27-FEB-26"), ("KCD7", "k")])

    with pytest.raises(ValueError, match="릴리스"):
        plan_supplement(primary, extra)


def test_plan_supplement_refuses_bundles_without_release_row(tmp_path):
    primary = _bundle(tmp_path / "new", [("SNOMED", "s")])
    extra = _bundle(tmp_path / "old", [("KCD7", "k")])

    with pytest.raises(ValueError, match="릴리스 행"):
        plan_supplement(primary, extra)
