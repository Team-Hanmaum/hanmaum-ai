# Spring ↔ FastAPI 분석 계약 v1

v1 내부 API의 요청·응답 규격이다. AI는 로컬·테스트용 `stub`과 `disabled`를 지원한다. stub은 빈 제안을 반환하고 운영은 v1 분석을 비활성화한다. 실제 모델을 사용하는 경로는 [v2 계약](ai-contract-v2.md)을 따른다. BE의 저장·반영은 [BE v1 계약](https://github.com/Team-Hanmaum/hanmaum-backend/blob/dev/docs/ai-contract.md)에 정리한다.

## 호출과 책임

- `POST /v1/analyses`에 camelCase JSON을 보낸다. `X-Internal-Api-Key`로 인증하고 `X-Request-Id`에는 본문 `requestId`와 같은 UUID를 넣는다.
- BE는 공간 권한과 후보를 확인한다. AI는 제안만 반환하며 BE가 검증·저장하고 반영 시 권한과 버전을 다시 검사한다.
- 합성 예시: [요청](../tests/fixtures/analysis-request.json), [응답](../tests/fixtures/analysis-response.json).

## 요청

| 필드 | 규칙 |
| --- | --- |
| `requestId`, `careSpaceId`, `recordId` | 필수 UUID |
| `content` | 공백뿐인 값 금지, 최대 10,000 UTF-16 코드 단위 |
| `recordedAt` | 시간대 오프셋이 있는 ISO 8601 일시 |
| `timezone` | 유효한 IANA 시간대 |
| `candidates` | 필수 배열, 50개 이하, `itemId` 중복 금지 |

후보는 `itemId`·`version`·`type`·`summary`를 모두 보낸다. `version`은 0 이상 Java Long 범위의 정수, `summary`는 공백뿐인 값 없이 최대 2,000 UTF-16 코드 단위다. 필드 누락, 미정의 필드, 잘못된 타입은 거부한다.

## 응답

`requestId`는 요청과 일치하며 `suggestions`는 최대 30개다. 제안의 모든 키가 필수이며 nullable 값은 `null`로 보낸다.

- `type`은 `SCHEDULE`·`TASK`·`QUESTION`·`OBSERVATION`·`GUIDANCE`, `operation`은 `CREATE`·`UPDATE`·`RESOLVE`다.
- `CREATE`의 `targetItemId`·`expectedVersion`은 `null`, `candidateItemIds`는 빈 배열이다.
- `UPDATE`·`RESOLVE`의 대상이 명확하면 후보 ID·버전·유형이 일치해야 하고 `candidateItemIds`는 비운다. 모호하면 대상·버전은 `null`, 같은 유형의 후보 ID를 1~50개 중복 없이 담는다.
- `title`은 공백뿐인 값 없이 최대 200 UTF-16 코드 단위다. `evidence`는 원문의 연속 구절이며 공백뿐인 값은 거부한다.
- `changes`는 최대 10개 필드이며 값은 각각 최대 2,000 UTF-16 코드 단위의 문자열이다.

AI의 v1 DTO는 세 작업을 검증하지만, BE의 v1 실행은 `CREATE`만 저장한다. 다른 작업은 `ANALYSIS_DEPENDENCY_UNAVAILABLE`로 처리한다.

## 상태 확인과 오류

| 경로·상태 | 의미 |
| --- | --- |
| `GET /health` → 200 | 서버 생존 확인 |
| `GET /health/ready` → 200/503 | v1 제공자 활성 여부 |
| 400 `REQUEST_ID_MISMATCH` | 헤더·본문 UUID 불일치 |
| 401 `UNAUTHORIZED` | 내부 키 누락·불일치 |
| 422 `INVALID_REQUEST` | 요청 계약 위반 |
| 502 `INVALID_AI_RESPONSE` | 제공자 결과 계약 위반 |
| 502 `AI_UNAVAILABLE` | 제공자 호출 오류 |
| 503 `AI_NOT_CONFIGURED` | 분석 비활성화 |
| 504 `AI_TIMEOUT` | 분석 대기 시간 초과 |

오류는 안전한 `code`와 `message`로 반환한다. v1의 분석 대기 시간은 기본 20초·최대 25초다. `requestId`는 요청·응답 대조용이며 v1에는 자동 재시도·결과 캐시·수신 기록을 통한 중복 차단이 없다.
