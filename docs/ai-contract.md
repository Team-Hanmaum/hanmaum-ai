# Spring ↔ FastAPI 분석 계약 v1

백엔드의 [초기 계약](https://github.com/Team-Hanmaum/hanmaum-backend/blob/chore/1-backend-settings/docs/ai-contract.md)과
DTO를 기준으로 구현합니다. 계약 변경 시 두 레포를 함께 확인합니다.
현재 제공자는 실제 분석 없이 빈 목록을 반환하는 로컬 전용 stub입니다.

## 호출과 책임

- `POST /v1/analyses`, JSON 본문, camelCase 필드 이름
- `X-Internal-Api-Key`: Spring과 공유하는 내부 인증키
- `X-Request-Id`: 본문의 `requestId`와 동일한 UUID
- Spring에서 공간 접근 권한을 확인한 뒤 같은 공간의 후보만 전달
- AI는 DB 접근이나 정보 확정 없이 제안만 반환
- Spring은 결과를 검증·저장하고, 사용자 확인 시 권한과 현재 DB 버전을 다시 검사

요청 예시는 [analysis-request.json](../tests/fixtures/analysis-request.json),
계약상 응답 예시는 [analysis-response.json](../tests/fixtures/analysis-response.json)을 참고합니다.
후자는 형식 검증용 합성 예시이며 stub이 생성하는 응답이나 실제 모델 품질의 증거는 아닙니다.

## 요청

| 필드 | 규칙 |
| --- | --- |
| `requestId`, `careSpaceId`, `recordId` | 필수 UUID |
| `content` | 공백뿐인 값 금지, 최대 10,000 UTF-16 단위 |
| `recordedAt` | 시간대 오프셋이 포함된 ISO 8601 일시 |
| `timezone` | 유효한 IANA 시간대, 예: `Asia/Seoul` |
| `candidates` | 필수 배열, 최대 50개, ID 중복 금지 |

후보의 `itemId`, `version`, `type`, `summary`는 필수입니다.
`version`은 0 이상 Java Long 범위의 정수입니다.
`summary`는 공백뿐인 값이 될 수 없고 최대 2,000 UTF-16 단위입니다.
Java의 문자열 길이 제한과 맞추기 위해 이모지는 종류에 따라 두 단위로 계산합니다.
필드 누락·알 수 없는 필드·문자열 대신 숫자를 넣는 등 잘못된 요청은 거부합니다.

시간·날짜가 불분명할 때 추측해 채우지 않는 정책은 실제 제공자 구현과 평가에서 검증해야 합니다.

## 응답

`requestId`는 요청과 같아야 하며 `suggestions`는 최대 30개의 제안 또는 빈 배열입니다.
각 제안의 모든 필드가 필수이며 nullable 필드도 키를 생략하지 않습니다.

- `type`: `SCHEDULE`, `TASK`, `QUESTION`, `OBSERVATION`, `GUIDANCE`
- `operation`: `CREATE`, `UPDATE`, `RESOLVE`
- `CREATE`: `targetItemId`·`expectedVersion`은 null, `candidateItemIds`는 빈 배열
- 확실한 `UPDATE`·`RESOLVE`: 요청 후보의 ID·버전·유형 일치, 후보 ID 목록은 빈 배열
- 모호한 `UPDATE`·`RESOLVE`: 대상 ID·버전은 null, 같은 유형의 요청 후보 ID를 하나 이상 반환
- `candidateItemIds`: 최대 50개, 중복 금지
- `title`: 공백뿐인 값 금지, 최대 200 UTF-16 단위
- `evidence`: 원문에 실제 존재하는 연속된 구절, 공백뿐인 값 금지
- `changes`: 최대 10개의 문자열 필드 값, 각 값은 최대 2,000 UTF-16 단위

`changes`의 유형별 허용 필드·날짜 형식·상태 전이 규칙은 확정 기능을 구현할 때 정의합니다.
현재 이 맵을 엔티티에 직접 적용하면 안 됩니다.
형식·대상·근거 검증이 의미 판단의 정확성을 보장하지 않으므로 모든 제안은 사용자 확인이 필요합니다.

## 상태 확인과 오류

| 경로·상태 | 의미 |
| --- | --- |
| `GET /health` → 200 | 서버 프로세스 응답 가능 |
| `GET /health/ready` → 200/503 | 제공자 활성 여부; 로컬 stub에서도 200 |
| 400 `REQUEST_ID_MISMATCH` | 헤더·본문 요청 ID 불일치 |
| 401 `UNAUTHORIZED` | 내부 키 누락·불일치 |
| 422 `INVALID_REQUEST` | 요청 계약 위반 |
| 502 `INVALID_AI_RESPONSE` | 제공자 결과의 형식·후보·근거 검증 실패 |
| 502 `AI_UNAVAILABLE` | 제공자 호출 오류 |
| 503 `AI_NOT_CONFIGURED` | 분석 비활성화 |
| 504 `AI_TIMEOUT` | 분석 대기 시간 초과 |

오류 본문은 `code`와 `message`로 구성합니다. 원문, 내부 키, 제공자의 오류 본문을 노출하지 않습니다.
현재 readiness는 활성 모드만 확인하며 외부 LLM의 연결 상태나 품질을 검사하지 않습니다.
운영에서는 API 문서 경로를 비활성화하고 내부 네트워크에서만 Spring의 호출을 받도록 배포합니다.

## 타임아웃·중복·후속 작업

분석 대기 시간 기본값은 20초, 최대 25초입니다. 백엔드의 30초 응답 제한보다 먼저 끝납니다.
시간 초과 시 로컬 비동기 작업을 취소합니다. 향후 외부 API를 연결하면 해당 API의 처리나 과금까지
취소된다고 보장할 수 없으므로 SDK 자체 타임아웃도 함께 설정해야 합니다.

현재 자동 재시도·결과 저장·중복 제거는 구현하지 않았습니다.
`requestId`는 지금 단계에서는 요청·응답을 대조하는 값이며, 중복 과금을 막는 보장은 없습니다.
실제 유료 모델을 연결하기 전에 동일 요청 재처리 정책을 구현하고 SDK 자동 재시도를 명시적으로 검토합니다.

후속 구현 범위:

1. LLM 제공자·모델 선정, 비밀키 주입, 구조화된 출력·프롬프트 구현
2. 기록 속 지시를 실행하지 않도록 원문을 데이터로 취급하고 도구·DB 접근 제한
3. 신규 항목·날짜 변경·질문 해소·모호한 후보·근거 없는 사실의 합성 평가 세트
4. 동일 `requestId`의 중복 처리·결과 보관 정책과 재시도 흐름
5. Spring의 원문·분석 상태·제안 저장 및 사용자 확정 기능
6. 실제 모델 연결 후 준비 상태, 동시 요청 제한, 비용·오류 관측과 배포 설정
