"""Synthea 값 → OMOP 표준 개념 ID.

매핑은 OHDSI 공식 ETL-Synthea(R)의 규칙을 따른다:
https://ohdsi.github.io/ETL-Synthea/
"""

from collections.abc import Iterable, Mapping
from typing import NamedTuple

GENDER_MALE = 8507
GENDER_FEMALE = 8532
GENDER = {"M": GENDER_MALE, "F": GENDER_FEMALE}

RACE_WHITE = 8527
RACE = {"white": RACE_WHITE, "black": 8516, "asian": 8515}

ETHNICITY_HISPANIC = 38003563
ETHNICITY = {"hispanic": ETHNICITY_HISPANIC, "nonhispanic": 38003564}

VISIT_INPATIENT = 9201
VISIT_OUTPATIENT = 9202
VISIT_EMERGENCY = 9203
VISIT = {
    "ambulatory": VISIT_OUTPATIENT,
    "wellness": VISIT_OUTPATIENT,
    "outpatient": VISIT_OUTPATIENT,
    "emergency": VISIT_EMERGENCY,
    "urgentcare": VISIT_EMERGENCY,
    "inpatient": VISIT_INPATIENT,
}

# Type Concept "EHR" — 모든 레코드가 전자의무기록에서 왔다는 뜻
TYPE_EHR = 32817

# Synthea SYSTEM 값 → 찾아볼 Athena vocabulary_id 후보 (앞에서부터 우선)
SYSTEM_VOCABULARIES = {
    "SNOMED-CT": ("SNOMED",),
    "ICD10": ("ICD10CM", "ICD10"),
}
DEFAULT_SYSTEM = "SNOMED-CT"


class ConceptMatch(NamedTuple):
    source_concept_id: int
    standard_concept_id: int


UNMAPPED = ConceptMatch(0, 0)

ConceptLookup = Mapping[tuple[str, str], ConceptMatch]

_LOOKUP_SQL = """
SELECT c.concept_code, c.concept_id, COALESCE(cr.concept_id_2, 0)
FROM {schema}.concept c
LEFT JOIN {schema}.concept_relationship cr
  ON cr.concept_id_1 = c.concept_id
 AND cr.relationship_id = 'Maps to'
 AND cr.invalid_reason IS NULL
WHERE c.vocabulary_id = %s AND c.concept_code = ANY(%s)
"""


def lookup_concepts(conn, schema: str, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], ConceptMatch]:
    """(Synthea SYSTEM, 코드) 쌍을 원천 개념과 'Maps to' 표준 개념으로 찾는다.

    어휘가 아직 적재되지 않았으면 빈 dict를 돌려주고, 호출 쪽은 0(미매핑)으로 채운다.
    """
    codes_by_system: dict[str, set[str]] = {}
    for system, code in keys:
        codes_by_system.setdefault(system, set()).add(code)

    found: dict[tuple[str, str], ConceptMatch] = {}
    with conn.cursor() as cur:
        for system, codes in codes_by_system.items():
            for vocabulary_id in SYSTEM_VOCABULARIES.get(system, ()):
                pending = sorted(code for code in codes if (system, code) not in found)
                if not pending:
                    break
                cur.execute(_LOOKUP_SQL.format(schema=schema), (vocabulary_id, pending))
                for code, source_id, standard_id in cur.fetchall():
                    # 한 원천 코드가 표준 개념 여러 개로 갈라지면 첫 번째만 쓴다 (ETL-Synthea와 같은 단순화)
                    found.setdefault((system, code), ConceptMatch(source_id, standard_id))
    return found
