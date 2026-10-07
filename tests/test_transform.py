from datetime import date, datetime

from omoplab.etl.concepts import (
    DEATH_CERTIFICATION,
    TYPE_EHR,
    ConceptMatch,
    ETHNICITY_HISPANIC,
    GENDER_FEMALE,
    GENDER_MALE,
    RACE_WHITE,
    TYPE_EHR_ENCOUNTER,
    TYPE_PERIOD,
    VISIT_EMERGENCY,
    VISIT_INPATIENT,
    VISIT_OUTPATIENT,
)
from omoplab.etl.transform import (
    to_conditions,
    to_deaths,
    to_observation_periods,
    to_persons,
    to_visits,
)


def _patient(pid, gender="M", race="white", ethnicity="nonhispanic", birth="1980-05-17"):
    return {"Id": pid, "BIRTHDATE": birth, "GENDER": gender, "RACE": race, "ETHNICITY": ethnicity}


def _encounter(eid, pid, start, stop, cls="ambulatory"):
    return {"Id": eid, "PATIENT": pid, "START": start, "STOP": stop, "ENCOUNTERCLASS": cls}


def _condition(code, system="SNOMED-CT", encounter="e1", start="2020-01-01", stop=""):
    return {"START": start, "STOP": stop, "PATIENT": "p-a", "ENCOUNTER": encounter, "SYSTEM": system, "CODE": code}


def test_persons_get_sequential_ids_and_standard_concepts():
    persons, id_map = to_persons([_patient("p-b", gender="F", ethnicity="hispanic"), _patient("p-a")])

    assert id_map == {"p-a": 1, "p-b": 2}
    first, second = persons
    assert (first.person_id, first.gender_concept_id, first.race_concept_id) == (1, GENDER_MALE, RACE_WHITE)
    assert first.year_of_birth == 1980 and first.month_of_birth == 5 and first.day_of_birth == 17
    assert second.gender_concept_id == GENDER_FEMALE
    assert second.ethnicity_concept_id == ETHNICITY_HISPANIC
    assert second.person_source_value == "p-b"


def test_person_values_match_case_insensitively():
    persons, _ = to_persons([_patient("p-1", gender="f", race="White", ethnicity="NonHispanic")])

    assert (persons[0].gender_concept_id, persons[0].race_concept_id) == (GENDER_FEMALE, RACE_WHITE)
    assert persons[0].ethnicity_concept_id != 0


def test_unknown_race_maps_to_zero_but_keeps_source_value():
    persons, _ = to_persons([_patient("p-1", race="hawaiian")])

    assert persons[0].race_concept_id == 0
    assert persons[0].race_source_value == "hawaiian"


def test_visit_class_maps_to_visit_concepts():
    encounters = [
        _encounter("e1", "p-a", "2020-01-01T09:00:00Z", "2020-01-01T09:30:00Z", "wellness"),
        _encounter("e2", "p-a", "2020-02-01T09:00:00Z", "2020-02-03T10:00:00Z", "Inpatient"),
        _encounter("e3", "p-a", "2020-03-01T09:00:00Z", "2020-03-01T11:00:00Z", "urgentcare"),
        _encounter("e4", "p-a", "2020-04-01T09:00:00Z", "2020-04-01T10:00:00Z", "hospice"),
        _encounter("e5", "p-a", "2020-05-01T09:00:00Z", "2020-05-01T10:00:00Z", "spaceship"),
    ]

    visits, visit_map = to_visits(encounters, {"p-a": 1})

    assert [v.visit_concept_id for v in visits] == [VISIT_OUTPATIENT, VISIT_INPATIENT, VISIT_EMERGENCY, 8546, 0]
    assert visit_map == {"e1": 1, "e2": 2, "e3": 3, "e4": 4, "e5": 5}
    assert visits[1].visit_start_datetime == datetime(2020, 2, 1, 9, 0)
    assert visits[1].visit_end_date == date(2020, 2, 3)
    assert all(v.visit_type_concept_id == TYPE_EHR_ENCOUNTER for v in visits)


def test_visits_for_unknown_patients_are_dropped():
    visits, visit_map = to_visits([_encounter("e1", "ghost", "2020-01-01T09:00:00Z", "2020-01-01T10:00:00Z")], {})

    assert visits == [] and visit_map == {}


def test_observation_period_spans_first_to_last_visit():
    visits, _ = to_visits(
        [
            _encounter("e1", "p-a", "2021-06-01T09:00:00Z", "2021-06-01T10:00:00Z"),
            _encounter("e2", "p-a", "2019-01-10T09:00:00Z", "2019-01-12T10:00:00Z"),
            _encounter("e3", "p-b", "2020-01-01T09:00:00Z", "2020-01-01T10:00:00Z"),
        ],
        {"p-a": 1, "p-b": 2},
    )

    periods = to_observation_periods(visits)

    assert [(p.person_id, p.observation_period_start_date, p.observation_period_end_date) for p in periods] == [
        (1, date(2019, 1, 10), date(2021, 6, 1)),
        (2, date(2020, 1, 1), date(2020, 1, 1)),
    ]
    assert all(p.period_type_concept_id == TYPE_PERIOD for p in periods)


def test_conditions_use_lookup_and_link_visit():
    rows = [_condition("44054006"), _condition("C01", system="ICD10", encounter="e-missing", stop="2020-02-10")]
    lookup = {("SNOMED-CT", "44054006"): ConceptMatch(201826, 201826, "Condition")}

    split = to_conditions(rows, {"p-a": 1}, {"e1": 7}, lookup)

    mapped, unmapped = split.conditions
    assert (mapped.condition_concept_id, mapped.condition_source_concept_id) == (201826, 201826)
    assert mapped.visit_occurrence_id == 7 and mapped.condition_end_date is None
    # 매핑이 안 된 코드는 버리지 않고 0으로 남겨 매핑률 측정에 드러나게 한다
    assert (unmapped.condition_concept_id, unmapped.condition_source_value) == (0, "C01")
    assert unmapped.visit_occurrence_id is None
    assert unmapped.condition_end_date == date(2020, 2, 10)


def test_conditions_route_by_target_domain():
    rows = [_condition("314529007"), _condition("73211009"), _condition("444")]
    lookup = {
        ("SNOMED-CT", "314529007"): ConceptMatch(4200001, 4200001, "Observation"),
        ("SNOMED-CT", "73211009"): ConceptMatch(201820, 201820, "Condition"),
        ("SNOMED-CT", "444"): ConceptMatch(4300001, 4300001, "Procedure"),
    }

    split = to_conditions(rows, {"p-a": 1}, {"e1": 7}, lookup)

    assert [c.condition_concept_id for c in split.conditions] == [201820]
    (obs,) = split.observations
    assert (obs.observation_concept_id, obs.observation_source_value, obs.visit_occurrence_id) == (4200001, "314529007", 7)
    assert obs.observation_type_concept_id == TYPE_EHR_ENCOUNTER
    assert split.skipped == 1


def test_deaths_come_from_patients_with_cause_from_death_certification():
    patients = [_patient("p-a"), {**_patient("p-b"), "DEATHDATE": "2021-03-04"}, {**_patient("p-c"), "DEATHDATE": "2022-01-01"}]
    for p in patients:
        p.setdefault("DEATHDATE", "")
    _, person_ids = to_persons(patients)
    encounters = [
        {**_encounter("e9", "p-b", "2021-03-04T10:00:00Z", "2021-03-04T10:15:00Z", "ambulatory"),
         "CODE": DEATH_CERTIFICATION, "REASONCODE": "254637007"},
        {**_encounter("e8", "p-b", "2020-01-01T10:00:00Z", "", "wellness"), "CODE": "410620009", "REASONCODE": "x"},
    ]
    lookup = {("SNOMED-CT", "254637007"): ConceptMatch(4115276, 4115276, "Condition")}

    deaths = to_deaths(patients, person_ids, encounters, lookup)

    assert [(d.person_id, d.death_date) for d in deaths] == [(2, date(2021, 3, 4)), (3, date(2022, 1, 1))]
    with_cause, without_cause = deaths
    assert (with_cause.cause_concept_id, with_cause.cause_source_value) == (4115276, "254637007")
    assert (without_cause.cause_concept_id, without_cause.cause_source_value) == (None, None)
    assert all(d.death_type_concept_id == TYPE_EHR for d in deaths)
