"""Synthea CSV 행(dict) → OMOP CDM 5.4 행(NamedTuple). DB에 의존하지 않는 순수 함수만 둔다.

각 NamedTuple의 필드 이름이 곧 CDM 컬럼 이름이라, 적재 단계는 `_fields`로 COPY 컬럼 목록을 만든다.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import NamedTuple

from omoplab.etl.concepts import (
    DEFAULT_SYSTEM,
    ETHNICITY,
    GENDER,
    RACE,
    TYPE_EHR_ENCOUNTER,
    TYPE_PERIOD,
    UNMAPPED,
    VISIT,
    ConceptLookup,
)


class Person(NamedTuple):
    person_id: int
    gender_concept_id: int
    year_of_birth: int
    month_of_birth: int
    day_of_birth: int
    birth_datetime: datetime
    race_concept_id: int
    ethnicity_concept_id: int
    person_source_value: str
    gender_source_value: str
    race_source_value: str
    ethnicity_source_value: str


class ObservationPeriod(NamedTuple):
    observation_period_id: int
    person_id: int
    observation_period_start_date: date
    observation_period_end_date: date
    period_type_concept_id: int


class Visit(NamedTuple):
    visit_occurrence_id: int
    person_id: int
    visit_concept_id: int
    visit_start_date: date
    visit_start_datetime: datetime
    visit_end_date: date
    visit_end_datetime: datetime
    visit_type_concept_id: int
    visit_source_value: str


class Condition(NamedTuple):
    condition_occurrence_id: int
    person_id: int
    condition_concept_id: int
    condition_start_date: date
    condition_end_date: date | None
    condition_type_concept_id: int
    visit_occurrence_id: int | None
    condition_source_value: str
    condition_source_concept_id: int


class Observation(NamedTuple):
    observation_id: int
    person_id: int
    observation_concept_id: int
    observation_date: date
    observation_type_concept_id: int
    visit_occurrence_id: int | None
    observation_source_value: str
    observation_source_concept_id: int


class ConditionSplit(NamedTuple):
    conditions: list[Condition]
    observations: list[Observation]
    skipped: int  # Condition·Observation 외 도메인으로 매핑돼 적재하지 않은 행 수


def _parse_timestamp(value: str) -> datetime:
    # Synthea는 UTC('Z')로 내보낸다. CDM 컬럼은 TIMESTAMP(시간대 없음)이라 UTC 그대로 둔다.
    return datetime.fromisoformat(value).replace(tzinfo=None)


def _parse_optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def to_persons(patients: Iterable[Mapping[str, str]]) -> tuple[list[Person], dict[str, int]]:
    """환자 ID 정렬 순서로 person_id를 매긴다 (같은 입력이면 같은 ID)."""
    ordered = sorted(patients, key=lambda p: p["Id"])
    id_map = {p["Id"]: i for i, p in enumerate(ordered, start=1)}
    persons = []
    for p in ordered:
        birth = date.fromisoformat(p["BIRTHDATE"])
        persons.append(
            Person(
                person_id=id_map[p["Id"]],
                gender_concept_id=GENDER.get(p["GENDER"].upper(), 0),
                year_of_birth=birth.year,
                month_of_birth=birth.month,
                day_of_birth=birth.day,
                birth_datetime=datetime(birth.year, birth.month, birth.day),
                race_concept_id=RACE.get(p["RACE"].lower(), 0),
                ethnicity_concept_id=ETHNICITY.get(p["ETHNICITY"].lower(), 0),
                person_source_value=p["Id"],
                gender_source_value=p["GENDER"],
                race_source_value=p["RACE"],
                ethnicity_source_value=p["ETHNICITY"],
            )
        )
    return persons, id_map


def to_visits(
    encounters: Iterable[Mapping[str, str]], person_ids: Mapping[str, int]
) -> tuple[list[Visit], dict[str, int]]:
    """방문 유형은 ENCOUNTERCLASS로 정한다. 모르는 유형(home, hospice 등)은 0으로 남겨 품질 점검에서 드러나게 한다."""
    visits: list[Visit] = []
    visit_map: dict[str, int] = {}
    for e in encounters:
        person_id = person_ids.get(e["PATIENT"])
        if person_id is None:
            continue
        visit_id = len(visits) + 1
        start = _parse_timestamp(e["START"])
        end = _parse_timestamp(e["STOP"]) if e["STOP"] else start
        visit_map[e["Id"]] = visit_id
        visits.append(
            Visit(
                visit_occurrence_id=visit_id,
                person_id=person_id,
                visit_concept_id=VISIT.get(e["ENCOUNTERCLASS"].lower(), 0),
                visit_start_date=start.date(),
                visit_start_datetime=start,
                visit_end_date=end.date(),
                visit_end_datetime=end,
                visit_type_concept_id=TYPE_EHR_ENCOUNTER,
                visit_source_value=e["ENCOUNTERCLASS"],
            )
        )
    return visits, visit_map


def to_observation_periods(visits: Iterable[Visit]) -> list[ObservationPeriod]:
    """사람마다 첫 방문 시작일 ~ 마지막 방문 종료일을 관찰기간 하나로 본다."""
    spans: dict[int, tuple[date, date]] = {}
    for v in visits:
        first, last = spans.get(v.person_id, (v.visit_start_date, v.visit_end_date))
        spans[v.person_id] = (min(first, v.visit_start_date), max(last, v.visit_end_date))
    return [
        ObservationPeriod(i, person_id, start, end, TYPE_PERIOD)
        for i, (person_id, (start, end)) in enumerate(sorted(spans.items()), start=1)
    ]


def to_conditions(
    rows: Iterable[Mapping[str, str]],
    person_ids: Mapping[str, int],
    visit_ids: Mapping[str, int],
    lookup: ConceptLookup,
) -> ConditionSplit:
    """Synthea conditions.csv를 표준 개념의 도메인에 따라 나눈다.

    Synthea의 '진단'에는 Medication review due(situation), 고용 상태(finding)처럼
    Athena에서 Observation 도메인인 개념이 섞여 있다. 도메인을 따르지 않으면 매핑률이 부풀려진다.
    - Condition 도메인, 또는 표준 개념이 없는 코드 → condition_occurrence (미매핑은 0으로 남겨 드러나게 한다)
    - Observation 도메인 → observation
    - 그 밖의 도메인(Procedure 등) → 이번 범위에서는 적재하지 않고 개수만 센다
    """
    conditions: list[Condition] = []
    observations: list[Observation] = []
    skipped = 0
    for r in rows:
        person_id = person_ids.get(r["PATIENT"])
        if person_id is None:
            continue
        system = r.get("SYSTEM") or DEFAULT_SYSTEM
        match = lookup.get((system, r["CODE"]), UNMAPPED)
        start = date.fromisoformat(r["START"])
        visit_id = visit_ids.get(r["ENCOUNTER"])
        if match.domain_id in (None, "Condition"):
            conditions.append(
                Condition(
                    condition_occurrence_id=len(conditions) + 1,
                    person_id=person_id,
                    condition_concept_id=match.standard_concept_id,
                    condition_start_date=start,
                    condition_end_date=_parse_optional_date(r["STOP"]),
                    condition_type_concept_id=TYPE_EHR_ENCOUNTER,
                    visit_occurrence_id=visit_id,
                    condition_source_value=r["CODE"],
                    condition_source_concept_id=match.source_concept_id,
                )
            )
        elif match.domain_id == "Observation":
            observations.append(
                Observation(
                    observation_id=len(observations) + 1,
                    person_id=person_id,
                    observation_concept_id=match.standard_concept_id,
                    observation_date=start,
                    observation_type_concept_id=TYPE_EHR_ENCOUNTER,
                    visit_occurrence_id=visit_id,
                    observation_source_value=r["CODE"],
                    observation_source_concept_id=match.source_concept_id,
                )
            )
        else:
            skipped += 1
    return ConditionSplit(conditions, observations, skipped)
