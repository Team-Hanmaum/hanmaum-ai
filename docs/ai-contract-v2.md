# BE↔AI 분석 계약 v2

공통 계약·fixture revision은 **v2-20261021.1**이다. 내부 호출 경로와 실행별 계약 버전에는 `v2`를 사용한다. [공통 fixture](../tests/fixtures/v2/README.md) · [v1 계약](ai-contract.md) · [연동 확인](v2-integration.md)

## 현재 AI 구현과 테스트 범위

AI는 v2 HTTP·공통 제한·SQLite 수신 기록·HMAC·동시성 제어를 구현한다. 기본 v2 제공자는 `disabled`이며, 로컬·테스트는 `stub`, 실제 분석은 `openai`를 선택한다. OpenAI에는 구조화된 출력 스키마와 필요한 분석 자료를 전달하고, 응답은 최초 요청 스냅샷에 대조한다. `GET /health/ready/v2`는 내부 인증 후 설정·저장소 접근을 확인한다.

AI HTTP 테스트는 A-* 146개, L-* 경계 96개, AI 담당 H-* 14개와 비교 불가 응답을 실행한다. L-* 나머지 6개는 측정 전용이다. 기존 B-* 29개는 SQLite·서비스 경로에서 실행하며, C-*의 BE 상태·저장·조회와 BE 전용 H-*는 BE가 검증한다. fixture의 초기 실행 범위 표기는 발행 당시 기준이다. 계약 검증과 실제 모델 품질 평가는 별개로 기록한다. 중복 JSON 키 검사는 원문 바이트에서 수행하므로 호출부는 `parse_request(raw)`로 요청을 읽고 응답 검증에도 같은 불변 스냅샷을 사용한다.

## 1. 계약 버전과 책임

- BE는 인증·공간 권한, 원본과 실행 상태, 후보·근거 구성, 응답의 독립 검증, 제안 저장·최종 반영·삭제 전파를 담당한다.
- AI는 주어진 자료로 새 항목·변경·답변·근거 연결을 제안한다. 내부 수신 기록으로 중복 모델 호출을 차단한다. 소식·근거의 문장을 시스템 지시나 도구·DB 호출 권한으로 해석하지 않는다.
- `/v1/analyses`는 [기존 계약](ai-contract.md)을 유지한다. 내부 계약 버전은 BE 공개 API 경로와 별개다. 한 실행은 하나의 계약 버전만 호출하며 자동 fallback은 없다.

## 2. 호출 형식

`POST /v2/analyses`에 비압축 UTF-8 JSON과 `Content-Type: application/json`을 사용한다. `X-Internal-Api-Key`로 인증하며, `X-Request-Id`는 본문 `requestId`와 UUID 값이 같아야 한다. 내부 키·모델 키·HMAC 키는 별도 값이다.

성공 본문은 `requestId`와 `suggestions`, 오류 본문은 `code`·`message`다. BE 공개 응답 래퍼는 사용하지 않는다. 로그와 오류 메시지에서 원문·인증키·제공자 원본 오류를 제외한다.

## 3. v2 요청

| 위치 | 필수 필드 |
| --- | --- |
| 루트 | `requestId`·`careSpaceId`·`recordId`(UUID), `content`, `recordedAt`, `timezone`(`Asia/Seoul`), `candidates` |
| 후보 | `itemId`(UUID), `version`(JSON 정수), `type`, `currentValues`, `basis` |
| 근거 | `basisId`(`care_item_basis.id`), `kind`(`SOURCE_RECORD`·`DIRECT_INPUT`), `text` |

후보 유형은 `SCHEDULE`·`TASK`·`QUESTION`·`OBSERVATION`·`GUIDANCE`다. camelCase 스키마에 없는 키, 잘못된 타입, 배열 내부의 중복 후보 ID 및 후보별 중복 근거 ID는 거부한다. v1의 `summary`로 v2의 현재 값과 근거를 대체하지 않는다. BE는 해당 공간의 유효한 후보와 근거만 보내고 AI는 입력에 없는 식별자·사실을 만들지 않는다.

- `SOURCE_RECORD.text`는 유효한 원본 근거 구절이다.
- `DIRECT_INPUT.text`는 독립적으로 저장한 직접 입력 근거 문서의 안정된 JSON 문자열이다. 현재 항목 문서와 구분한다.
- 근거를 교체할 때는 새 문서·연결 ID를 사용한다. 기존 `basisId`를 다른 항목·문서·출처에 재사용하지 않는다.
- 상대 날짜는 원본 `recordedAt`과 `Asia/Seoul`로 해석한다. 날짜·시각·장소·담당자 등 원문에 없는 값은 추정하지 않는다.

## 4. currentValues의 정확한 형태

유형별 키를 모두 보낸다. 일반 텍스트는 문자열 또는 `null`, 날짜는 유효한 `YYYY-MM-DD` 또는 `null`, `scheduledTime`은 유효한 `HH:mm` 또는 `null`이다. `observedTimeText`는 원문 시각 표현, `assigneeMembershipId`는 기존 담당자 UUID 또는 `null`이다. 누락, 자동 타입 변환, 다른 유형의 키는 허용하지 않는다.

| 유형 | 필수 키 | `status` 값 |
| --- | --- | --- |
| `SCHEDULE` | title, description, status, scheduledDate, scheduledTime, location | SCHEDULED, COMPLETED, CANCELED |
| `TASK` | title, description, status, dueDate, assigneeMembershipId | NOT_STARTED, IN_PROGRESS, COMPLETED |
| `QUESTION` | title, description, status, answerText | UNCONFIRMED, CONFIRMED |
| `OBSERVATION` | title, description, observedDate, observedTimeText | 해당 키 없음 |
| `GUIDANCE` | title, description, sourceName, receivedDate | 해당 키 없음 |

`status`가 있는 유형은 `null`을 허용하지 않는다. BE는 소유자가 확정한 질문 답변만 `currentValues.answerText`에 담는다. `CONFIRMED`와 `answerText: null` 조합도 유효하며, 값이 제공되지 않은 경우 기존 답변의 유무·일치·상충을 추정하지 않는다. 새 미확정 답변은 응답의 `changes.answerText`로만 제안한다.

## 5. v2 응답

`requestId`는 요청과 같아야 한다. `suggestions`는 배열이며 빈 배열은 성공이다. 각 제안은 `operation`, `type`, `targetItemId`, `expectedVersion`, `candidateItemIds`, `title`, `evidence`, `changes`, `clearFields`, `relationKind`, `reviewReasons`, `basisReferences`를 모두 포함한다. 없는 대상은 `null`, 빈 목록은 `[]`, 빈 변경은 `{}`로 표현한다.

`evidence`는 이번 `content`에 실제로 있는 연속된 구절이며 공백뿐인 값은 거부한다. `title`은 제안 목록의 표시 제목이고 항목 제목 변경은 `changes.title`이다. 검토 사유가 없어도 사용자 검토를 거친다.

## 6. 작업·관계·대상 조합

| 작업 | 허용 유형 | 허용 관계 |
| --- | --- | --- |
| `CREATE` | 모두 | `NEW` |
| `UPDATE` | 모두 | `RELATED`, `CONFLICT`, `UNDETERMINED` |
| `RESOLVE` | `QUESTION` | `RELATED`, `CONFLICT`, `UNDETERMINED` |
| `LINK_EVIDENCE` | 모두 | `DUPLICATE`, `UNDETERMINED` |

| 대상 | `targetItemId`·`expectedVersion` | `candidateItemIds` | 대상 검토 사유 |
| --- | --- | --- | --- |
| 신규 `CREATE` | 둘 다 `null` | 빈 배열 | 없음 |
| 명확 | 입력 후보의 ID·버전, 같은 유형 | 빈 배열 | 없음 |
| 모호 | 둘 다 `null` | 같은 유형의 입력 후보 1개 이상 | `TARGET_AMBIGUOUS` |
| 찾지 못함 | 둘 다 `null` | 빈 배열 | `TARGET_NOT_FOUND` |

- `reviewReasons`와 ID 목록은 중복을 허용하지 않는다. 두 대상 검토 사유는 함께 사용할 수 없다.
- `UNDETERMINED`는 검토 사유가 하나 이상 필요하다. `CONFLICT` 관계는 `CONFLICT` 사유와 비교 근거가 필요하다.
- `VALUE_UNCLEAR`는 값이 불명확할 때, `ASSIGNEE_UNRESOLVED`는 담당자 확인이 필요한 `TASK`에만 사용한다.
- 모호한 대상이나 대상 없음은 유효한 검토 제안으로 표현한다.

## 7. changes와 clearFields

`CREATE`·`UPDATE`의 공통 허용 필드는 `title`·`description`이다. 추가 허용 필드는 유형별로 구분한다.

| 유형 | 추가 필드 |
| --- | --- |
| `SCHEDULE` | scheduledDate, scheduledTime, location, status |
| `TASK` | dueDate, status |
| `QUESTION` | 없음 |
| `OBSERVATION` | observedDate, observedTimeText |
| `GUIDANCE` | sourceName, receivedDate |

- `changes`는 허용된 필드의 비어 있지 않은 문자열 값만 담는다. 날짜·시각·상태 형식을 검증한다. `CREATE`에서 빠진 값은 미상, `UPDATE`에서 빠진 값은 유지다. 새 값이 미정이면 기존 값을 삭제하거나 임의 값으로 채우지 않는다.
- `clearFields`는 명시적 삭제를 제안하는 `UPDATE`에서만 허용한다. 같은 필드의 중복과 `changes`와의 교집합은 거부한다. 실제 삭제 가능 여부와 필수값은 BE의 최종 반영 검증을 따른다.
- `assigneeMembershipId`는 입력으로만 사용한다. 담당자 지정·변경·해제는 `ASSIGNEE_UNRESOLVED`로 사람이 검토한다.
- `RESOLVE`는 `changes.answerText`만 허용한다. 답변은 공백뿐인 값 없이 필수이며 `clearFields`는 빈 배열이다. 제안 저장은 답변 확정과 상태 전환에 선행한다.
- `LINK_EVIDENCE`는 `changes = {}`와 `clearFields = []`를 사용한다. 근거 연결은 사용자 최종 반영에서 처리한다.
- 유효한 상태값만으로 전이를 허용하지 않는다. 날짜가 지났다는 이유로 완료를 제안하지 않는다.

## 8. 빈 UPDATE의 예외

`changes`와 `clearFields`가 모두 빈 `UPDATE`는 `ASSIGNEE_UNRESOLVED` 또는 `VALUE_UNCLEAR`가 있고 대상·관계·유형·근거 규칙도 만족할 때만 허용한다. 대상 사유만으로는 부족하다. 사람이 검토할 제안으로 남기고 빈 변경을 피하기 위한 임의 필드를 만들지 않는다.

## 9. basisReferences

각 항목은 `{itemId, basisIds}`다. 후보별로 한 번만 등장하며 `basisIds`는 비어 있지 않고 중복 없이 해당 입력 후보에 속해야 한다. ID는 UUID 값으로 비교한다.

- 명확한 `UPDATE`·`RESOLVE`·`LINK_EVIDENCE`는 선택한 대상의 근거를 하나 이상 참조한다. 모호한 제안은 `candidateItemIds`의 후보마다 하나 이상 참조한다.
- 실제 비교에 사용한 다른 후보의 근거도 추가할 수 있다. `CREATE`와 `TARGET_NOT_FOUND`는 빈 참조를 허용한다.
- `CONFLICT`는 이번 원문의 `evidence`와 비교 가능한 기존 근거를 모두 요구한다.
- 비교 근거는 변경 대상·권한·자동 연결을 결정하지 않는다. 참조의 존재와 소속은 계약 검증, 의미 적절성은 모델 평가로 확인한다.

## 10. requestId, 재시도, 수신 기록

BE는 실행마다 `requestId`를 발급한다. FE `clientRequestId`와 역할이 다르다. 명시적 재시도는 새 실행·새 ID와 원래 `recordedAt`을 사용하며, 후보와 계약 버전은 새 실행 기준으로 정한다. 자동 재시도, 모델 SDK 자동 재시도, 같은 ID의 재호출, v1 fallback을 제공하지 않는다.

기존 수신 기록은 이전 분석의 실행 상태와 관계없이 적용한다. 등록과 기존 기록 비교 규칙은 [16.4절](#164-기존-기록-우선과-원자적-등록)을 따른다. AI는 SQLite에 비교 메타데이터와 HMAC만 저장한다. 보관 중인 기록을 기준으로 호출을 차단하며 결과 캐시나 정확히 한 번 과금을 보장하지 않는다. [수신 기록 안내](v2-receipts.md)

## 11. 동일 본문 판정

- 동일: 외부 JSON 객체 키 순서, 같은 디코딩 문자열의 이스케이프 표현, 선언된 UUID 필드의 대소문자, `recordedAt`의 같은 시점을 나타내는 오프셋 표현.
- 다름: 문자열 공백·줄바꿈·문자, 배열 순서, 값·버전·근거 변경, `DIRECT_INPUT.text` 내부 JSON의 공백·키 순서.
- 객체 키를 재귀 정렬하고 선언된 UUID만 표준 형태로 바꾼다. 문자열·배열은 정규화하지 않는다. 중복 JSON 키는 객체 변환 전에 거부한다.
- `recordedAt`은 초와 시간대 오프셋이 있는 ISO 8601 문자열이다. 소수초는 생략하거나 1~6자리만 허용하고, 비교는 UTC·소수초 6자리·`Z`로 통일한다. 시간대 없는 값, 숫자 타임스탬프, 소수초 7자리 이상은 거부한다.

안정된 UTF-8 표현을 HMAC으로 비교하며 수신 기록은 `(계약 버전, requestId)`로 식별한다. 기존 규칙·키를 사용할 수 없으면 비교를 차단한다. 비교용 키는 AI 전용이며 BE나 내부 인증키와 공유하지 않는다.

## 12. 실행별 계약 버전과 BE 저장

- BE는 접수 시 계약 버전을 고정하고 완료까지 유지한다. 명시적 새 실행은 현재 활성 버전을 사용한다. v1 실행·저장 데이터는 v1 계약으로 해석한다.
- BE는 v2 결과 전체를 독립 검증하고 네 작업의 대상·버전·후보·관계·사유·변경·삭제·비교 근거를 검토 제안으로 저장한다. 하나라도 무효거나 저장이 실패하면 전체를 거부한다.
- 완료 시 공간·요청자 참여, 원본과 전달한 모든 후보·문서·근거·출처·버전을 다시 확인한다. 입력이 무효화되면 결과를 저장하지 않는다.
- 근거 연결 해제는 `care_item_basis` 행 제거를 의미한다. 원래 소식·evidence·직접 입력 문서는 별개다. 직접 입력 근거는 현재 항목 문서와 별도로 검사하고, 응답에서 참조하지 않은 입력 근거도 대상이다.
- 저장 뒤 무효화된 근거·제안은 정상 검토 대상으로 노출하지 않는다. 원본 삭제는 답변·변경분·비교 내용 등 파생 정보에도 적용한다. 삭제된 원본의 공개 분석 조회는 `404 RESOURCE_NOT_FOUND`다. 내부 실행 상태는 별도로 검증한다.
- 공개 조회는 활성 구성원의 권한을 확인하고 현재 값·제안 변경·삭제 필드·미확정 답변을 구분한다. 대상이 모호하거나 버전이 바뀌면 확정 대상이나 병합 값을 추정하지 않는다.
- 최종 반영은 BE가 소유자 권한·구성원 예외·잠금·버전을 다시 확인한 뒤 선택 묶음 단위로 원자 처리한다.

## 13. AI 오류와 BE 실패 코드

전체 허용 목록이다. 기계 판독 기준은 fixture의 [error-mapping.json](../tests/fixtures/v2/error-mapping.json)이다. 상태와 코드를 쌍으로 검사한다.

| AI HTTP | AI code | BE 통신 어댑터 실패 코드 |
| --- | --- | --- |
| 400 | REQUEST_ID_MISMATCH | AI_INTEGRATION_ERROR |
| 401 | UNAUTHORIZED | AI_INTEGRATION_ERROR |
| 413 | REQUEST_TOO_LARGE | AI_INTEGRATION_ERROR |
| 422 | INVALID_REQUEST | AI_INTEGRATION_ERROR |
| 409 | REQUEST_ALREADY_RECEIVED | AI_DUPLICATE_REQUEST |
| 409 | REQUEST_ID_REUSED | AI_REQUEST_ID_REUSED |
| 502 | INVALID_AI_RESPONSE | AI_INVALID_RESPONSE |
| 502 | AI_UNAVAILABLE | AI_SERVICE_UNAVAILABLE |
| 503 | AI_NOT_CONFIGURED | AI_INTEGRATION_ERROR |
| 503 | RECEIPT_STORE_UNAVAILABLE | AI_RECEIPT_STORE_UNAVAILABLE |
| 503 | REQUEST_COMPARISON_UNAVAILABLE | AI_RECEIPT_STORE_UNAVAILABLE |
| 503 | AI_OVERLOADED | AI_SERVICE_UNAVAILABLE |
| 504 | AI_TIMEOUT | AI_TIMEOUT |

필드·개수·문자열 합계 초과는 `422`, 요청 전송 바이트 초과는 `413`다. BE가 전송 전에 한도를 넘겼으면 호출 없이 `AI_INTEGRATION_ERROR`, 수신 중 응답 바이트 초과는 상태와 무관하게 `AI_INVALID_RESPONSE`다. 알려지지 않은 조합이나 잘못된 오류 본문은 `AI_SERVICE_UNAVAILABLE`로 처리한다.

접수된 소식의 저장 성공은 분석 실패로 바뀌지 않는다. 접근 가능한 활성 원본의 분석 상세는 실행 실패도 `200`의 실행 상태와 실패 코드로 조회한다.

## 14. 공통 fixture 구성과 판정

| 범위 | 검증 |
| --- | --- |
| A-* | 요청·응답 계약, 가짜 제공자 호출 횟수와 오류, 혼합 결과의 전체 거부 |
| B-* | 동일성·중복·비교/저장소 실패, 경쟁·재시작·키 버전 |
| C-* | BE 권한·버전·근거 무효화·삭제·저장 롤백·공개 조회 |
| L-* | 바이트·UTF-16·개수 경계 및 동반 위반 |
| H-* | 오류 조합, 청크 수신, 슬롯·과부하·취소와 부분 저장 차단 |

같은 원문과 기대값을 공유한다. 중복 키·이스케이프·시간 정밀도 사례에서는 문자열을 다시 직렬화하지 않는다. 실행 방법과 판정 기준은 [공통 fixture 문서](../tests/fixtures/v2/README.md)를 따른다.

## 15. 미정 사항과 구현 준비 완료 기준

공통 한도와 검사 방법은 16절을 따른다. 운영 한도 조정·후보 선별, 수신 기록 보관/정리·키 교체, 운영 규모·품질 목표, 제품 필수값·삭제 가능 필드·질문 재개 정책은 각각 별도로 결정한다. 구현 상태·계약 검증·배포 연동·모델 품질은 별도로 기록한다.

## 16. 초기 로컬·통합 연동 합의 — v2-20261021.1

제한 설정이나 revision을 요청 DTO에 추가하지 않는다.

### 16.1 공통 상한과 계산

상한과 같으면 허용한다. 모든 제한을 동시에 적용한다. 기계 판독 기준은 [limits.json](../tests/fixtures/v2/limits.json)이다.

| 대상 | 상한 |
| --- | ---: |
| 실제 요청 본문 | 1,048,576바이트 |
| 실제 성공·오류 응답 본문 | 524,288바이트 |
| 요청 content | 10,000 UTF-16 코드 단위 |
| 요청 candidates | 100개 |
| 후보별 basis / 요청 전체 basis | 20개 / 500개 |
| basis.text 각각 | 20,000 UTF-16 코드 단위 |
| currentValues의 자유 서술 문자열 각각 | 10,000 UTF-16 코드 단위 |
| 요청 전체 문자열 값 합계 | 100,000 UTF-16 코드 단위 |
| 응답 suggestions | 30개 |
| 제안 표시용 title | 500 UTF-16 코드 단위 |
| evidence | 10,000 UTF-16 코드 단위 |
| changes의 자유 서술 문자열 각각 | 10,000 UTF-16 코드 단위 |
| 응답 전체 문자열 값 합계 | 50,000 UTF-16 코드 단위 |
| 제안별 candidateItemIds / basisReferences 항목 | 각각 100개 |
| 참조 항목별 basisIds / 제안별 basisIds 합계 | 20개 / 500개 |

- 바이트는 실제 비압축 UTF-8 JSON 본문 기준이다. 공백·이스케이프를 포함하고 HTTP 헤더를 제외한다.
- 문자열은 JSON 디코딩 후 타입 변환·UUID/날짜 정규화 전의 UTF-16 코드 단위로 센다. 배열을 포함한 모든 문자열 값의 반복 등장을 각각 세며 키·null·숫자·boolean은 제외한다. trim·유니코드 정규화·`DIRECT_INPUT.text` 재파싱은 하지 않는다.
- 자유 서술 필드는 유형·작업별 허용 필드 중 `title`, `description`, `location`, `observedTimeText`, `sourceName`, `answerText`다. 최상위 제안 제목과 `changes.title`의 제한은 별도다.
- 다른 제안에서 같은 근거를 다시 참조할 수 있으며 매번 합계에 포함한다. 제공자 결과와 최종 전송 본문은 AI가, 수신한 원문은 BE가 각각 검사한다.

### 16.2 저장 정책과 분석 범위

분석 한도는 공개 저장 한도와 별개다. 초과 입력은 원본을 보존하고 분석만 실패 처리한다. 후보·근거의 일부 제외, 절삭, 요약, 빈 제안 성공 처리를 금지한다. BE는 구성 단계에서 규모를 제한하고 AI는 수신 단계에서 바이트를 제한한다. 자동 압축 해제로 제한을 우회하지 않는다.

### 16.3 오류 매핑

`413 REQUEST_TOO_LARGE`, `503 REQUEST_COMPARISON_UNAVAILABLE`, `503 AI_OVERLOADED`를 포함한 전체 허용 목록과 경계 처리는 [13절](#13-ai-오류와-be-실패-코드)을 따른다. 제공자 결과 크기·개수·계약 위반은 `502 INVALID_AI_RESPONSE`다.

### 16.4 기존 기록 우선과 원자적 등록

1. 인증·검증 후 기록을 먼저 확인한다. 같은 ID·같은 본문은 `409 REQUEST_ALREADY_RECEIVED`, 다른 본문은 `409 REQUEST_ID_REUSED`, 안전한 비교 불가는 `503 REQUEST_COMPARISON_UNAVAILABLE`다. 저장소 오류는 `503 RECEIPT_STORE_UNAVAILABLE`다. 이 판정은 제공자 슬롯 부족보다 우선한다.
2. 신규 요청은 제공자 슬롯을 확보한다. 실패하면 `503 AI_OVERLOADED`를 반환하며 해당 요청은 등록하거나 호출하지 않는다. 경쟁 요청이 등록한 기록과는 별개다.
3. 확보 후 원자적 등록에서 기존 기록을 다시 확인한다. 최초 등록에 성공한 요청만 호출한다. 등록 뒤 실패·취소·타임아웃·과대 결과에도 기록을 보존한다.

본문 수신·파싱 자원은 제공자 슬롯과 별도로 제한한다.

### 16.5 시간·슬롯·재시도와 AI 내부 책임

- 제공자 대기 기본 20초·최대 25초, BE 연결 3초·전체 HTTP 기한 30초다. AI의 현재 전체 처리 예산은 26초, 응답 전송 예산은 0.5초다.
- 초기 제공자 슬롯은 단일 ASGI 앱·워커 기준 2개이며 대기열이 없다. 작업이 실제로 끝날 때까지 슬롯을 유지한다. 응답 타임아웃이 제공자 처리나 과금 취소를 보장하지 않는다.
- 동일 입력의 크기 초과와 기존 기록의 비교 불가를 자동 재시도나 새 ID 발급으로 우회하지 않는다. 복구 시점을 모르는 경우 `Retry-After`를 임의로 정하지 않는다.
- 저장소 초기화·권한·잠금, HMAC 키 관리, 수신 보호, 제공자 응답·토큰 제한, 민감 정보를 제외한 진단은 AI 책임이다. 보관 정책 확정 전에 자동 삭제를 도입하지 않는다.

### 16.6 fixture와 구현 순서

공통 파일·기대값·revision은 양쪽에서 동일하게 관리한다. [revision-history.json](../tests/fixtures/v2/revision-history.json)은 이전 커밋과 사례 변경을 기록한다. [GENERATION.md](../tests/fixtures/v2/GENERATION.md)에 따라 경계 본문의 길이·SHA-256·UTF-16 측정값을 재현한다.

경계 사례는 해당 상한 외 조건을 만족하도록 구성하고, 불가능하면 `coupledViolations`로 동반 위반을 표시한다. 기대값을 구현 결과에 맞춰 완화하지 않는다. 검증은 명세 일관성, AI HTTP·슬롯, BE 실행·DB 처리, v1 회귀와 모델 품질을 각각 판정한다.
