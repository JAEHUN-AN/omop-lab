# omop-lab

OMOP CDM 5.4를 손으로 익히고, 그 위에서 **한국 진단코드(KCD) → 국제 표준 개념 매핑**을 측정하는 실습 리포.

## 왜

- 의료 데이터 표준화의 핵심은 테이블 변환보다 **용어 매핑**이다. 병원마다 다른 코드를 같은 개념으로 묶어야 다기관 비교가 맞는다.
- 의료와 보험은 **KCD 진단코드와 EDI 청구코드**라는 같은 언어를 쓴다. 둘 다 OHDSI Athena에 어휘로 올라가 있다.

## 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Synthea 합성 환자 → CDM 핵심 테이블 ETL (person, visit_occurrence, observation_period, condition_occurrence, observation) | ✅ 어휘 없이 적재 확인 |
| 0.5 | Athena 어휘 적재 → 진단 매핑률 측정 (SNOMED, ICD-10 → 표준 개념) | ⏳ 어휘 다운로드 대기 |
| 0.9 | DataQualityDashboard로 품질 점검, 공식 ETL-Synthea(R) 결과와 비교 | |
| 1 | KCD 코드 목록 → KCD7 → 표준 개념 매핑률 측정 도구 | |

## 데이터 경계 (중요)

- 이 리포에는 **합성 데이터와 공개 어휘만** 들어간다. 생성물은 커밋하지 않는다(`data/`, `vocab/`).
- 회사·고객 병원 데이터는 **절대 커밋하지 않는다.** 1단계에서 실제 코드 목록을 쓸 때는 `private/`(gitignore)에만 두고,
  리포에는 집계 수치도 남기지 않는다.

## 실행

준비물: Docker, Java 17+, [uv](https://docs.astral.sh/uv/)

```bash
cp .env.example .env            # 비밀번호를 바꾼다
docker compose up -d            # Postgres 16, localhost:15432
uv sync

# 합성 환자 1,000명 생성 (시드 고정이라 매번 같은 데이터)
curl -L -o tools/synthea-with-dependencies.jar \
  https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar
java -Duser.timezone=UTC -jar tools/synthea-with-dependencies.jar -p 1000 -s 42 -cs 42 -r 20261007 \
  --exporter.baseDirectory=data/synthea --exporter.csv.export=true \
  --exporter.fhir.export=false --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false Massachusetts

uv run omoplab init-db          # CDM 5.4 테이블 생성 (이미 있으면 --force 필요 — 어휘까지 지운다)
uv run omoplab load-vocab       # vocab/ 의 Athena CSV를 한 트랜잭션으로 적재한 뒤 PK·인덱스 생성
uv run omoplab etl              # Synthea → CDM
uv run omoplab status           # 행 수, 진단 매핑률
```

### Athena 어휘 받기

1. <https://athena.ohdsi.org> 가입 → Download
2. 기본 선택에 더해 **SNOMED, ICD10, ICD10CM, LOINC, RxNorm, KCD7, EDI** 를 고른다
   (CPT4는 UMLS 라이선스가 필요하니 빼도 된다)
3. 메일로 온 zip을 `vocab/`에 풀고 `uv run omoplab load-vocab` → `uv run omoplab etl` 다시 실행

## 설계 결정 (공식 ETL-Synthea와 다른 점)

나중에 공식 ETL과 숫자를 비교할 때, 차이가 아래 의도한 단순화에서만 나와야 한다.

| 항목 | 여기 | ETL-Synthea | 이유 |
|---|---|---|---|
| 방문 | encounter 1건 = 방문 1건 | 같은 날 ER·OP, 하루 이내 inpatient를 합친다 | 학습 단계에서는 원천과 1:1이 추적하기 쉽다 |
| 진단의 도메인 | Condition → condition_occurrence, Observation → observation, 그 밖 도메인은 개수만 센다 | Condition 도메인만 condition_occurrence로 보낸다 | Synthea "진단" 중 disorder는 20.6%뿐이고 finding 42.5%, situation 22.7%다(예: Medication review due). 도메인을 무시하면 진단 테이블이 오염된다 |
| 미매핑 진단 | 0으로 condition_occurrence에 남긴다 | 버린다 | 매핑률 측정이 이 리포의 목적이다 |
| 일대다 'Maps to' | concept_id가 가장 작은 표준 개념 하나 | 모두 행으로 만든다 | 결과가 결정적이게 하려고 |
| ICD10 | ICD10CM에서만 찾는다 | 같다 | WHO ICD10으로 폴백하면 같은 문자열의 다른 개념에 붙을 수 있다 |
| Type Concept | 방문·진단·관찰 32827, 관찰기간 32882 | 같다 | |
| 사망(death) | 적재 안 함 | 적재 | 다음 단계 |

**Synthea는 반드시 `-Duser.timezone=UTC`로 돌린다.** 방문 시각은 UTC로, 진단 날짜는 JVM 시간대로 찍힌다.
한국 시간대로 돌렸을 때는 진단 날짜가 연결된 방문보다 하루 늦은 건이 14,884건이었고, 관찰기간 밖에서 시작하는 진단이 234건 나왔다.
UTC로 돌리면 관찰기간 밖 진단은 0건이 된다. 남는 하루 차이 1,156건(2.7%)은 Synthea 자체 특성이다
(치은염·스트레스·고용 상태 같은 코드가 wellness 방문 다음 날짜로 찍힌다).

## 테스트

```bash
uv run pytest --cov=omoplab     # DB 통합 테스트는 Postgres가 떠 있을 때만 돈다 (test_cdm 스키마 사용)
```

## 참고

- CDM DDL: [OHDSI/CommonDataModel v5.4.2](https://github.com/OHDSI/CommonDataModel) (`ddl/`, Apache-2.0). FK 제약은 적용하지 않는다.
- 매핑 규칙: [ETL-Synthea](https://ohdsi.github.io/ETL-Synthea/) (공식 R 구현)을 따른다.
- [The Book of OHDSI](https://ohdsi.github.io/TheBookOfOhdsi/)
- 한국 EDI 어휘 통합: [EDI2OMOP (OHDSI 2024)](https://ohdsi.org/wp-content/uploads/2024/10/29-Park-yiju_EDI2OMOP_2024Symposium-Yiju-Park.pdf)
