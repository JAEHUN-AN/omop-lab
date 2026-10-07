"""Synthea 값 → OMOP 표준 개념 ID.

개념 ID와 Type Concept은 OHDSI 공식 ETL-Synthea(R, v5.4 SQL)와 같게 맞춘다:
https://ohdsi.github.io/ETL-Synthea/
나중에 공식 ETL 결과와 비교할 때 차이가 의도한 단순화에서만 나오게 하려는 것이다.
"""

from collections.abc import Iterable, Mapping
from typing import NamedTuple

from omoplab.ddl import validate_schema

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

# Type Concept — 레코드의 출처. ETL-Synthea와 같은 값
TYPE_EHR_ENCOUNTER = 32827  # 방문·진단·관찰: "EHR encounter record"
TYPE_PERIOD = 32882  # 관찰기간

# Synthea SYSTEM 값 → 찾아볼 Athena vocabulary_id 후보 (앞에서부터 우선).
# Synthea 3.x의 ICD10은 ICD-10-CM이다. WHO ICD10으로 폴백하면 같은 문자열의 다른 개념에 붙을 수 있어서 하지 않는다.
SYSTEM_VOCABULARIES = {
    "SNOMED-CT": ("SNOMED",),
    "ICD10": ("ICD10CM",),
}
DEFAULT_SYSTEM = "SNOMED-CT"


class ConceptMatch(NamedTuple):
    source_concept_id: int
    standard_concept_id: int
    domain_id: str | None  # 표준 개념의 도메인. 표준 개념이 없으면 None


UNMAPPED = ConceptMatch(0, 0, None)

ConceptLookup = Mapping[tuple[str, str], ConceptMatch]

# 'Maps to' 대상은 유효한 표준 개념(standard_concept='S')만 인정한다.
# 일대다 매핑이면 concept_id가 가장 작은 것 하나만 쓴다 (ETL-Synthea는 모두 행으로 만든다 — 의도한 단순화).
_LOOKUP_SQL = """
SELECT c.concept_code, c.concept_id, t.concept_id, t.domain_id
FROM {schema}.concept c
LEFT JOIN {schema}.concept_relationship cr
  ON cr.concept_id_1 = c.concept_id
 AND cr.relationship_id = 'Maps to'
 AND cr.invalid_reason IS NULL
LEFT JOIN {schema}.concept t
  ON t.concept_id = cr.concept_id_2
 AND t.standard_concept = 'S'
 AND t.invalid_reason IS NULL
WHERE c.vocabulary_id = %s AND c.concept_code = ANY(%s)
ORDER BY c.concept_code, t.concept_id NULLS LAST
"""


def lookup_concepts(conn, schema: str, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], ConceptMatch]:
    """(Synthea SYSTEM, 코드) 쌍을 원천 개념과 'Maps to' 표준 개념으로 찾는다.

    어휘가 아직 적재되지 않았으면 빈 dict를 돌려주고, 호출 쪽은 0(미매핑)으로 채운다.
    """
    sql = _LOOKUP_SQL.format(schema=validate_schema(schema))
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
                cur.execute(sql, (vocabulary_id, pending))
                for code, source_id, standard_id, domain_id in cur.fetchall():
                    found.setdefault((system, code), ConceptMatch(source_id, standard_id or 0, domain_id))
    return found
