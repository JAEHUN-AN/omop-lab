# omop-lab

OMOP CDM 5.4를 손으로 익히고, 그 위에서 **한국 진단코드(KCD) → 국제 표준 개념 매핑**을 측정하는 실습 리포.

## 왜

- 의료 데이터 표준화의 핵심은 테이블 변환보다 **용어 매핑**이다. 병원마다 다른 코드를 같은 개념으로 묶어야 다기관 비교가 맞는다.
- 의료와 보험은 **KCD 진단코드와 EDI 청구코드**라는 같은 언어를 쓴다. 둘 다 OHDSI Athena에 어휘로 올라가 있다.

## 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Synthea 합성 환자 → CDM 핵심 테이블 ETL (person, visit_occurrence, observation_period, condition_occurrence) | ✅ 어휘 없이 적재 확인 |
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
java -jar tools/synthea-with-dependencies.jar -p 1000 -s 42 -cs 42 -r 20261007 \
  --exporter.baseDirectory=data/synthea --exporter.csv.export=true \
  --exporter.fhir.export=false --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false Massachusetts

uv run omoplab init-db          # CDM 5.4 스키마 생성 (기존 데이터 삭제)
uv run omoplab load-vocab       # vocab/ 의 Athena CSV 적재
uv run omoplab etl              # Synthea → CDM
uv run omoplab status           # 행 수, 진단 매핑률
```

### Athena 어휘 받기

1. <https://athena.ohdsi.org> 가입 → Download
2. 기본 선택에 더해 **SNOMED, ICD10, ICD10CM, LOINC, RxNorm, KCD7, EDI** 를 고른다
   (CPT4는 UMLS 라이선스가 필요하니 빼도 된다)
3. 메일로 온 zip을 `vocab/`에 풀고 `uv run omoplab load-vocab` → `uv run omoplab etl` 다시 실행

## 테스트

```bash
uv run pytest --cov=omoplab     # DB 통합 테스트는 Postgres가 떠 있을 때만 돈다 (test_cdm 스키마 사용)
```

## 참고

- CDM DDL: [OHDSI/CommonDataModel v5.4.2](https://github.com/OHDSI/CommonDataModel) (`ddl/`, Apache-2.0). FK 제약은 적용하지 않는다.
- 매핑 규칙: [ETL-Synthea](https://ohdsi.github.io/ETL-Synthea/) (공식 R 구현)을 따른다.
- [The Book of OHDSI](https://ohdsi.github.io/TheBookOfOhdsi/)
- 한국 EDI 어휘 통합: [EDI2OMOP (OHDSI 2024)](https://ohdsi.org/wp-content/uploads/2024/10/29-Park-yiju_EDI2OMOP_2024Symposium-Yiju-Park.pdf)
