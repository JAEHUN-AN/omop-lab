# omop-lab

OMOP CDM 5.4를 손으로 익히고, 그 위에서 **한국 진단코드(KCD) → 국제 표준 개념 매핑**을 측정하는 실습 리포.

## 왜

- 의료 데이터 표준화의 핵심은 테이블 변환보다 **용어 매핑**이다. 병원마다 다른 코드를 같은 개념으로 묶어야 다기관 비교가 맞는다.
- 의료와 보험은 **KCD 진단코드와 EDI 청구코드**라는 같은 언어를 쓴다. 둘 다 OHDSI Athena에 어휘로 올라가 있다.

## 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | Synthea 합성 환자 → CDM 핵심 테이블 ETL (person, visit_occurrence, observation_period, condition_occurrence, observation, death, cdm_source) | ✅ |
| 0.5 | Athena 어휘 적재 → 진단 매핑률 측정 (SNOMED, ICD-10 → 표준 개념) | ✅ 99.3% (아래 측정 기록) |
| 0.9 | DataQualityDashboard로 품질 점검, 공식 ETL-Synthea(R) 결과와 비교 | ✅ 적용 검사 통과율 98.86%(공식 98.78%), 공식 진단 조합의 99.9%가 일치 |
| 1 | 코드 목록(KCD 진단·EDI 행위/검사/약제) → 국내 어휘 → 표준 개념 연결률 측정 (`measure`) | ✅ 실제 기관 목록 측정 완료 (결과 비공개) |

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

### 코드 목록 측정 (1단계)

```bash
uv run omoplab measure private/codes.csv --out private/result.csv
```

입력 CSV — **실제 기관 데이터는 반드시 `private/`(gitignore) 아래에 둔다.** 리포 안의 다른 위치면 거부한다(`--force`로만 허용).

| 열 | 필수 | 설명 |
|---|---|---|
| `code` | ✅ | 원내 코드. `E119`·`e11.9`처럼 점·대소문자가 달라도 맞춰 읽는다 |
| `system` | | `KCD` 또는 `EDI`. 없으면 `--system`으로 준다 |
| `group` | | 병원 등 비교 단위 |
| `count` | | 환자수·건수. 있으면 **사용량 가중 연결률**을 함께 낸다 |
| `name` | | 코드명. 결과 CSV로 옮겨진다 |

UTF-8(BOM 포함)과 CP949(엑셀 기본 저장) 모두 읽는다.

판정 순서: **정확 일치 → 상위 코드 일치 → 미발견**.

- 상위 코드 일치는 **KCD만** 한다. 소수점 아래를 한 자리씩 떼어 3자리 분류까지 올라간다.
  `E11.9A`, `E11.900123`처럼 KCD 뒤에 병원 고유 자릿수를 붙인 **원내 확장 코드**도 이렇게 찾는다.
- EDI는 하지 않는다. 접두사를 자르면 약제 코드가 혈구 검사 코드에 붙는 식으로 다른 분류에 붙는다 (실측).
- 숫자로 시작하거나 문자가 깨진 코드는 **형식오류**로 세고 찾지 않는다. KCD 검표·별표(`†`, `*`)는 떼고 읽는다.
- **연결**은 찾은 국내 코드에 유효한 표준 개념 'Maps to'가 있는 경우다. 폐기된 국내 코드는 따로 센다.
- 측정 전에 필요한 어휘와 'Maps to'가 적재돼 있는지 확인하고, 보고서 첫 줄에 어휘 버전을 찍는다.
- 화면에는 코드만 찍고 코드명은 `--show-names`일 때만 찍는다. 입력·결과 경로가 리포 안이면서 `private/` 밖이면 거부한다.

```
어휘: EDI EDI 2026-01-15, KCD7 7th revision
group  체계   코드 형식오류 원내확장 어휘정확 어휘상위 미발견 폐기 연결(정확) 연결(상위)  연결%  가중%
데모A  EDI    1893     1218        0      589        0   1304  303        528          0   27.9   26.6
데모A  KCD     107        0        3       94       12      1    0         82          9   85.0   82.8
```
공개 어휘에서 고른 코드에 표기 변형(점 생략, 뒤에 자릿수 덧붙임, 없는 코드)을 섞은 데모 입력이다.
EDI는 자릿수를 덧붙인 코드를 상위 탐색하지 않으므로 미발견으로 남는다 — 의도한 동작이다.

### Athena 어휘 받기

1. <https://athena.ohdsi.org> 가입 → Download
2. 기본 선택에 더해 **SNOMED, ICD10, ICD10CM, LOINC, RxNorm, KCD7, EDI** 를 고른다
   (CPT4는 UMLS 라이선스가 필요하니 빼도 된다)
3. 메일로 온 zip을 `vocab/`에 풀고 `uv run omoplab load-vocab` → `uv run omoplab etl` 다시 실행

어휘를 나눠 받았으면 **같은 릴리스일 때만** 합칠 수 있다. 기본 묶음에 없는 어휘만 덧붙인다:

```bash
uv run omoplab load-vocab vocab/<EDI가 든 묶음> --add vocab/<KCD7이 든 묶음>
```

인덱스가 이미 있는 상태에서 다시 적재하면 느리다(7분 → 29분). 처음부터 다시 넣을 거라면 `init-db --force` 후 적재하고 `etl`을 다시 돌린다.

### R 도구 — 품질 점검(DQD)과 공식 ETL 비교

준비물: R 4.4+ (Windows는 `winget install RProject.R`). 패키지는 리포 전용 `.rlib/`에 설치된다(gitignore, Python의 `.venv` 역할).

```bash
Rscript r/install.R              # DatabaseConnector, DataQualityDashboard, ETLSyntheaBuilder + PostgreSQL JDBC
Rscript r/run_dqd.R              # cdm 스키마 품질 점검 → data/dqd/cdm.json
Rscript r/run_etl_synthea.R      # 공식 ETL-Synthea → cdm_official (어휘는 cdm을 가리키는 뷰, 약 9분)
Rscript r/run_dqd.R cdm_official
uv run omoplab compare cdm_official   # 행 수와 (환자·개념·날짜) 대조
```

`init-db --force`로 cdm을 다시 만들면 cdm_official의 어휘 뷰도 지워진다 → `run_etl_synthea.R`를 다시 돌린다.

## 측정 기록

### 0.5 — Synthea 진단 매핑 (2026-10-07, Athena 다운로드 2026-10-07)

| 항목 | 값 |
|---|---|
| 어휘 적재 | concept 651만, concept_relationship 3,996만, concept_ancestor 7,671만 행 (7분 24초) |
| 진단 원천 42,704행 → condition_occurrence | 19,082 |
| → observation (Observation 도메인) | 23,589 (55%) — 도메인 분리를 안 했다면 전부 진단으로 잘못 들어갔다 |
| → 적재 안 함 (그 밖 도메인) | 33 |
| 표준 개념 매핑률 | **99.3%** |

미매핑 0.7%의 원인:
- SNOMED `55680006`(Drug overdose), `6525002`(Dependent drug abuse) 등은 **폐기(invalid_reason=D)된 코드이고 대체 개념도 없다.** Synthea가 옛 SNOMED 코드를 쓴다.
- ICD10CM `C77.0`, `C79.51`(이차성 악성 신생물)은 받은 묶음 안에 'Maps to'가 없다. 전이 코드는 Cancer Modifier 어휘로 가는 경우가 있어 그 어휘를 받지 않은 탓으로 보인다 (미확인).

### KCD7 어휘 자체의 표준 개념 연결률

| 항목 | 값 |
|---|---|
| KCD7 코드 | 22,508 |
| 유효한 표준 개념으로 'Maps to'가 있는 코드 | 17,983 (**79.9%**) |
| 대상 | SNOMED Condition 14,862 · SNOMED Observation 2,807 · Procedure 171 · Measurement 105 |

미연결이 몰린 장: **S 손상 66%, U 특수목적 84%, W·X·Y 외인 33~50%**.
U는 MERS(`U19`) 같은 한국 고유 코드와 한의 변증 코드라 국제 표준에 짝이 없다.
→ 실제 병원 코드를 KCD7로 매핑할 때 **손상·외인·한국 고유 코드가 구조적 공백**이 된다. Athena에는 KCD7만 있으므로 KCD 8차 이후 신설 코드도 따로 따져야 한다.

### EDI(건강보험 청구코드)·KCD7 표준 개념 연결률 (2026-10-07)

Athena에서 EDI를 따로 받은 묶음에는 KCD7·ICD10이 빠져 있어, 같은 릴리스(v5.0 29-AUG-26)의 이전 묶음에서
`load-vocab --add`로 보충했다 (KCD7 22,508 · ICD10 16,638 개념, 관계 178,670건).

**EDI 코드의 54%는 폐기(invalid_reason=D)된 옛 코드라 유효·폐기를 나눠 봐야 한다.**

| EDI 도메인 | 유효 코드 | 유효 연결률 | 폐기 코드 | 폐기 연결률 | 연결 대상 |
|---|---:|---:|---:|---:|---|
| Procedure (행위) | 146,159 | 99.6% | 172,797 | 76.1% | SNOMED |
| Drug (약제) | 22,900 | 100% | 42,732 | 100% | RxNorm |
| Device (치료재료) | 31,874 | 100% | 5,122 | 100% | SNOMED, RxNorm Extension |
| Measurement (검사) | 1,648 | 100% | 19,319 | **5.0%** | LOINC |
| KCD7 (진단) | 22,508 | 79.9% | — | — | SNOMED |

- 현행 EDI는 거의 전부 표준 개념으로 이어진다. 공백은 **폐기된 옛 코드**, 특히 옛 검사 코드에 있다.
  과거 데이터나 옛 코드가 남은 원내 마스터를 다룰 때 문제가 된다 → `measure`가 폐기 코드를 따로 센다.
- (정정) 처음에는 폐기 코드를 구분하지 않고 "검사 연결률 12.4%"로 적었다. 코드 리뷰에서 바로잡았다.

### 0.9 — 데이터 품질(DQD)과 공식 ETL-Synthea 비교 (2026-10-07)

같은 Synthea CSV를 우리 Python ETL(`cdm`)과 공식 ETL-Synthea 2.1(`cdm_official`)로 변환했다.

**DataQualityDashboard 2.9.0 (검사 2,384개)**

| | cdm (이 리포) | cdm_official (공식) |
|---|---:|---:|
| 적용된 검사 | 788 | 1,560 |
| **적용된 검사 기준 통과율** | **98.86%** | **98.78%** |
| 실패 | 9 | 19 |
| (참고) DQD 기본 통과율 | 99.62% | 99.20% |

DQD 기본 통과율(`percentPassed`)은 "해당 없음" 검사(빈 테이블 등)까지 통과로 센다. 이 리포는 빈 테이블이 많아
해당 없음이 1,596개(공식 824개)라 기본 통과율이 부풀려진다. **적용된 검사만 보면 두 ETL은 사실상 같다.**
`r/run_dqd.R`이 두 값을 모두 찍는다.

- 이 리포의 실패 9개
  - 6개: 아직 변환하지 않은 테이블(약물·시술·검사·era)이 비어 있다. 범위 문제다.
  - 2개: 1950년 이전 날짜. Synthea가 100세가 넘는 가상 환자를 만든다.
  - 1개: drug_strength의 성분 개념 분류. 어휘 쪽 문제다.
  - 방문 유형 매핑을 넓히기 전에는 방문 개념 실패 2개가 더 있었다(99.54%).
- 공식 ETL의 주요 실패
  - 선택 항목(admitted_from, discharged_to, condition_status)을 NULL 대신 0으로 채워 표준 개념 완전성 검사에 걸린다.
  - observation 33만 행의 유형 개념이 유효한 표준 개념이 아니다.
  - 공식 쪽은 테이블을 훨씬 많이 채운다. 이 리포가 테이블을 늘리면 검사가 더 적용되므로 통과율은 달라질 수 있다.

**레코드 대조** (`omoplab compare cdm_official`). person_id는 ETL마다 달라서 원천 환자 ID로 맞춘다.
서로 다른 조합 수 기준이고, 방문 연결·종료일·source_value는 대조하지 않는다.

| 대조 (환자 + 열) | cdm | cdm_official | 양쪽 | 공식 기준 | cdm 기준 | 차이 원인 |
|---|---:|---:|---:|---:|---:|---|
| 진단 (개념, 시작일) | 19,082 | 18,786 | 18,772 | **99.9%** | 98.4% | 이 리포는 미매핑 진단을 0으로 남긴다 |
| 관찰 (개념, 날짜) | 23,589 | 336,087 | 23,128 | 6.9% | **98.0%** | 공식은 observations.csv까지 변환한다. 이 리포는 진단에서 온 것만 있다 |
| 방문 (개념, 시작일) | 73,717 | 69,022 | 69,022 | **100%** | 93.6% | 방문을 합치지 않고, home 등 4유형을 더 남긴다 |
| 사망 (날짜) | 148 | 137 | 6 | 4.4% | 4.1% | 공식은 사망진단 방문 날짜를 쓴다 — 실측 0~14일(평균 5.6일) 늦고, 137명 중 131명이 다르다 |
| 사망 (사인) | 148 | 137 | 137 | **100%** | 92.6% | 사인은 같다. 이 리포에만 있는 11명은 사인 매핑이 없어 공식이 버린 사람 |

→ **공식 ETL이 만든 진단 (환자, 개념, 시작일) 조합의 99.9%가 이 리포에도 있다.** 나머지 차이는 아래 설계 결정에 적은 의도된 차이에서 나온다.

## 설계 결정 (공식 ETL-Synthea와 다른 점)

나중에 공식 ETL과 숫자를 비교할 때, 차이가 아래 의도한 단순화에서만 나와야 한다.

| 항목 | 여기 | ETL-Synthea | 이유 |
|---|---|---|---|
| 방문 | encounter 1건 = 방문 1건 | 같은 날 ER·OP, 하루 이내 inpatient를 합친다 | 학습 단계에서는 원천과 1:1이 추적하기 쉽다 |
| 방문 유형 | home·hospice·snf·virtual도 표준 Visit 개념(Home Visit, Hospice, Skilled Nursing Facility, Telehealth)으로 | 이 넷은 방문으로 만들지 않는다 | 0으로 남기면 DQD가 방문 개념 완전성 실패로 잡는다 |
| 진단의 도메인 | Condition → condition_occurrence, Observation → observation, 그 밖 도메인은 개수만 센다 | Condition 도메인만 condition_occurrence로 보낸다 | Synthea "진단" 중 disorder는 20.6%뿐이고 finding 42.5%, situation 22.7%다(예: Medication review due). 도메인을 무시하면 진단 테이블이 오염된다 |
| 미매핑 진단 | 0으로 condition_occurrence에 남긴다 | 버린다 | 매핑률 측정이 이 리포의 목적이다 |
| 일대다 'Maps to' | concept_id가 가장 작은 표준 개념 하나 | 모두 행으로 만든다 | 결과가 결정적이게 하려고 |
| ICD10 | ICD10CM에서만 찾는다 | 같다 | WHO ICD10으로 폴백하면 같은 문자열의 다른 개념에 붙을 수 있다 |
| Type Concept | 방문·진단·관찰 32827, 관찰기간 32882 | 같다 | |
| 사망 날짜 | patients.DEATHDATE (실제 사망일) | 사망진단 방문 날짜 (실측: 사망일보다 0~14일, 평균 5.6일 늦음) | 사망진단서 발급일이 아니라 사망일이 맞다. 대신 사망진단 방문 135건이 사망일 뒤에 놓인다 — "사망 후 방문 없음"을 가정하는 분석은 이 방문을 빼야 한다 (DQD는 사망 후 60일까지 허용해서 통과) |
| 사망자 범위 | 사망일이 있는 전원. 사인은 가장 이른 사망진단 방문의 REASONCODE | 사인이 표준 개념으로 매핑된 사람만 | 사인 매핑 실패로 사망자를 버리면 사망률이 틀어진다 (실측 11명 차이). 사망진단 방문이 없으면 사인 NULL(7명), 사인 코드가 폐기됐거나 Condition 도메인이 아니면 개념 0(4명) |

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
- 매핑 규칙: [ETL-Synthea](https://ohdsi.github.io/ETL-Synthea/) (공식 R 구현)을 따르고, 다른 점은 설계 결정 표에 적었다.
- 품질 점검: [DataQualityDashboard](https://github.com/OHDSI/DataQualityDashboard)
- [The Book of OHDSI](https://ohdsi.github.io/TheBookOfOhdsi/)
- 한국 EDI 어휘 통합: [EDI2OMOP (OHDSI 2024)](https://ohdsi.org/wp-content/uploads/2024/10/29-Park-yiju_EDI2OMOP_2024Symposium-Yiju-Park.pdf)
