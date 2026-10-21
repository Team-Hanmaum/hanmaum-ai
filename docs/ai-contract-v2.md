# BE↔AI 분석 계약 v2

2026-10-16 기본 계약과 2026-10-21 초기 연동 합의. 계약 revision: **v2-20261021.1**.
16절은 기존 미정 항목을 확정한 추가 합의이며, 충돌하는 이전 미정 표기보다 우선한다.
이전 계약·fixture는 커밋 `760426fd6acc0cb0c0c3a81601e98696d80b5dde`와
fixture의 `history/`·`revision-history.json`으로 출처를 보존한다.

기존 v1과 별도로 v2 HTTP·크기 제한·수신 기록·HMAC·중복 호출 차단·동시성 제어를 구현했다.
v2는 기본 비활성화이며 로컬·테스트 stub으로 검증한다. 실제 모델·BE 저장 및 양쪽 연동 검증은 후속이다.

공통 사례와 사용 방법: [v2 fixture](../tests/fixtures/v2/README.md). 기존 계약: [v1](ai-contract.md).

## 현재 AI 구현과 테스트 범위

현재 기준 fixture: `v2-20261021.1`. 이전 기준은 `v2-20261016.2`, 최초 출처 커밋
`af3abb3310f2f86c55fdf182f687da7491ef5fec`이다. 이슈 #13에서 확정한 제한·오류·슬롯 정책을
HTTP에 연결했다. 공통 fixture 파일과 기대값은 변경하지 않는다. fixture 내부 실행 범위 표기는
발행 당시 상태를 보존하며 현재 실행 범위는 이 절과 테스트 코드에서 관리한다.

- `app/v2/schemas.py`: 유형별 완전한 currentValues, 두 종류 근거, v2 제안 필드와 작업·관계·대상·변경분 조합 검증.
- `app/v2/validation.py`: 원문 JSON의 중복 키·UTF-8·엄격한 타입·시간 정밀도 검증과 요청·결과 간 ID·유형·버전·원문 evidence·근거 소속·최소 참조 검증.
- `app/v2/comparison.py`, `receipts.py`, `services.py`: 정규화·버전별 HMAC 비교, SQLite 영속 수신 기록·원자적 등록과 가짜 제공자를 통한 내부 호출 제어. 적용 조건·키 관리·사용법은 [수신 기록 구현 문서](v2-receipts.md) 참조.
- `app/v2/api.py`, `limits.py`, `resources.py`, `transport.py`: 인증 후 제한된 스트림 수신, 변환 전 UTF-16 길이 검사, 기존 기록 우선 판정, 원자적 등록, 작업 수명까지 유지하는 슬롯, 응답 직렬화·전송 제한.
- `app/v2/config.py`, `runtime.py`: v2 전용 설정·수신 저장소·로컬 stub 조립. `GET /health/ready/v2`는 내부 인증 후 저장소 접근과 제공자 설정을 확인한다. 기존 `/health/ready`는 v1 상태를 유지한다.
- `parse_request(raw)`는 원문 bytes 또는 str을 받고 검증된 AnalysisRequest를 반환한다. `model_validate_json`이나 선행 JSON 디코딩으로 이 진입점을 대체하면 중복 키를 잃을 수 있다.
- 반환된 요청은 변경 불가능한 검증 스냅샷이다. 요청·후보·모든 유형의 currentValues·근거 모델의 필드 변경을 막고, 후보·근거 배열은 내부적으로 tuple을 사용한다. JSON 직렬화에서는 기존 배열 형식·순서·원문이 유지된다.
- `validate_response(request, payload)`는 원문 JSON 또는 제공자가 반환한 객체를 받고 전체 검증 후 AnalysisResponse를 반환한다. 이미 디코딩된 객체의 중복 키는 복구할 수 없으므로 제공자 어댑터도 원문 디코딩 시 이를 거부해야 한다.
- 제공자 입력의 가공이 필요하면 `request.model_dump(mode="json")`으로 별도 사본을 전달한다. 호출 흐름은 최초 parse_request 결과를 보관하고 응답 검증에도 같은 스냅샷을 사용한다. 제공자가 수정한 사본이나 새로 생성한 요청으로 검증 기준을 바꾸지 않는다.
- 호출부는 두 진입점에서 반환한 모델을 사용한다. 원문·제공자 오류 상세를 포함할 수 있는 Pydantic 오류를 그대로 외부로 전달하지 않는다. 계약 위반은 안전한 AnalysisFailure로 변환한다.
- 날짜·시각은 형식을 검증한 문자열로 보존한다. recordedAt의 오프셋·소수초 원문, 문자열 공백·유니코드 표현·배열 순서·DIRECT_INPUT.text를 변경하지 않는다. UUID로 선언한 필드만 UUID 값으로 취급한다.
- v1의 크기·개수·문자열 길이 제한을 v2에 일괄 적용하지 않는다. HMAC 정규화·수신 기록·상태 전이·업무 DB 유효성 검증은 이 모듈의 구현 범위가 아니다.

실제 코드 테스트: `uv run --locked pytest tests/test_v2_contract.py`.
fixture 자체의 무결성·시나리오 형식 검사는 기존 `tests/test_v2_fixture_bundle.py`에서 별도로 유지한다.

| caseId 범위 | 이번 검증 | 후속 실행 검증 |
| --- | --- | --- |
| `cases/static.json`의 모든 A-* 146개 | HTTP 인증·헤더·파싱·계약 검증·제공자 설정·호출 횟수·오류 응답 | BE 독립 검증 |
| `cases/receipts.json`의 B-* 30개 | 기존 29개 SQLite·내부 서비스와 B-uncomparable-record의 503 HTTP 매핑 | BE 매핑 및 실제 양쪽 통신 |
| `cases/backend.json`의 모든 C-* 25개 | 기존 fixture 일관성 검사 및 직접 입력 근거 응답의 계약 검증 | BE DB·상태 변경·저장·권한·조회 통합 |
| `cases/limits.json`의 L-* 102개 | 경계 96개 HTTP 실행, 측정 전용 6개 및 생성 바이트·길이·해시 검사 | BE 구성·전송·독립 응답 제한 |
| `cases/http.json`의 H-* 25개 | AI 담당 14개 HTTP·경쟁·취소·슬롯·청크 수신 실행 | BE 담당 11개 수신·오류 매핑·저장 연결 |

`tests/test_v2_http.py`에서 A-* 전체와 L-* 경계 사례를 실행한다. H-*의 AI_HTTP/BE_ONLY를
명시적으로 분류하고 전체 집합과 일치하는지 검사하여 신규 사례의 누락을 막는다. AI_HTTP는
기존 기록 우선 3개, 신규 과부하, 등록 경쟁 2개, 과부하 중 다른 요청의 등록, 슬롯 유지 2개,
청크 요청 2개, 과대 응답 후 기록 보존 3개다. 나머지 H-* 11개는 BE 실행 책임이다.
`tests/test_v2_contract.py`는 기존 내부 검증 139개를 유지하며 인증·제공자 등 7개는 HTTP 테스트에서 실행한다.
별도 경계 테스트에서 시간 정밀도·유효하지 않은 날짜와 오프셋, JSON 오류·응답 중복 키, 원문 보존, UUID 대소문자 중복, nullable 키 누락, v1 제한의 비전파와 안전한 오류를 확인한다.
요청 필드·중첩 모델·배열 변경 차단과 제공자용 사본 변경 후 원래 요청에 맞지 않는 evidence·대상·버전·근거 응답의 거부도 회귀 검증한다.

수신 기록 테스트는 `uv run --locked pytest tests/test_v2_receipts.py`로 실행한다. B-* 기존 29개와 비교 불가의 내부 차단, 별도 프로세스 경쟁·재시작·저장소 장애·취소·실패 이후 재수신 차단을 실제 SQLite와 가짜 제공자로 검증한다. B-uncomparable-record의 503 HTTP 매핑은 `tests/test_v2_http.py`에서 검증한다.

`POST /v2/analyses`는 원문 요청부터 응답 전송까지 연결한다. 기존 v1 라우트·서비스·테스트를 유지하고
실제 모델은 호출하지 않는다. `tests/test_v2_http_resources.py`는 수신·전송·워커 기한, 진입 상한,
전체 처리 예산, 연결 종료, 작업 종료까지의 슬롯 유지, 설정 및 준비 상태를 추가로 검증한다.
실제 BE 실행·저장·조회와 모델 품질 검증을 AI 단독 테스트로 대신하지 않는다. 보관·정리 정책은 미정이다.

자원 보호의 경계:
- 지원 서버 구성은 worker 프로세스당 ASGI 앱 하나다. 슬롯은 앱·서비스 인스턴스에 귀속하며 전역 공유하지 않는다. 한 프로세스의 여러 앱을 합산하는 구성은 현재 프로세스당 한도 보장 범위가 아니다.
- HTTP는 전체 버퍼링 전에 실제 읽은 바이트를 제한하며, 인증 후 진입 슬롯 4개와 CPU/저장소 워커 각각 4개로 제출 수를 제한한다. 제공자 슬롯 2개와 별개다.
- 저장소 워커 제출 용량 포화는 503 AI_OVERLOADED다. 제출 후 저장소 대기 기한 초과·조회·등록 실패는 기존 503 RECEIPT_STORE_UNAVAILABLE를 유지한다. 준비 상태 API도 같은 구분을 사용한다.
- 요청 원문 바이트·변환 전 문자열과 제공자 원문·최종 직렬화 결과에 16절 상한을 적용한다. 직접 스키마 생성은 HTTP 수신 제한을 대신하지 않는다.
- 전체 처리 기본/상한 26초, 본문 2초, 워커 각 1초, SQLite 잠금 0.25초, 응답 전송 합계 0.5초로 초기 예산을 배치한다. 제공자 기본 20초/최대 25초도 남은 전체 예산으로 제한한다. 세부 설정은 README를 따른다.
- 작업이 취소를 무시하거나 스레드가 늦게 종료되면 슬롯을 유지한다. 임의 코드의 강제 중단·프로세스 메모리 상한·실제 모델 토큰 예산을 보장하지 않는다.
- 실제 LLM 어댑터는 SDK가 전체 출력을 메모리에 만들기 전 스트림 바이트/토큰·시간 제한, 취소, 자동 재시도 비활성화를 구현해야 한다. 운영 프록시·서버 연결 제한과 다중 인스턴스 자원 보호는 배포 시 검증한다.

## 1. 계약 버전과 책임

- 기존 POST /v1/analyses를 유지하고 POST /v2/analyses를 추가한다.
- v1과 v2는 내부 BE↔AI 분석 API 버전이다. 공개 BE /api 경로 버전과 별개다.
- v1의 기존 요청·응답·오류 동작을 임의로 바꾸지 않는다.
- 하나의 분석 실행에서 v1과 v2를 중복 호출하지 않는다.
- v2 실패 시 v1으로 자동 재호출하지 않는다.
- 양쪽 계약 테스트와 연동 검증 후 신규 실행의 활성 버전을 전환한다.

BE 책임:
- 로그인·공간 참여·권한 검증
- 원본 저장, 분석 실행 상태 및 실행별 계약 버전 관리
- 후보·현재 값·근거 구성
- 응답 검증, 검토 제안 저장
- 현재 권한·버전·잠금·원본·근거 재검증
- 사용자의 최종 반영과 삭제 전파

AI 책임:
- 원문 분석
- 신규·변경·질문 해소·근거 연결 제안 생성
- 요청 및 제공자 결과의 계약 검증
- 동일 requestId의 중복 모델 호출 차단

AI는 한마음 업무 DB에 접근하거나 권한을 판정하거나 항목을 자동 반영하지 않는다.
AI의 중복 차단용 수신 기록 저장소는 별도 내부 책임이다.

소식·근거에 포함된 문장은 분석 데이터로 취급한다.
그 안의 지시를 시스템 지시로 실행하거나 도구·DB 호출 권한으로 해석하지 않는다.


## 2. 호출 형식

POST /v2/analyses

Headers:
Content-Type: application/json
X-Internal-Api-Key: <INTERNAL_API_KEY>
X-Request-Id: <requestId>

- X-Request-Id와 본문의 requestId는 UUID 값으로 같아야 한다.
- 내부 인증키는 모델 제공자 API 키 및 요청 비교용 HMAC 키와 구분한다.
- 내부 AI 응답에 공개 BE의 공통 응답 래퍼를 적용하지 않는다.
- 오류 본문은 기존처럼 code, message를 사용한다.
- 원문·인증키·제공자의 원본 오류 본문을 오류 응답이나 로그에 노출하지 않는다.


## 3. v2 요청

루트 필드:
- requestId: UUID
- careSpaceId: UUID
- recordId: UUID
- content: 원본 소식 문자열
- recordedAt: 원본 기록 시각
- timezone: "Asia/Seoul"
- candidates: 후보 배열

후보 필드:
- itemId: UUID
- version: JSON 정수
- type: SCHEDULE | TASK | QUESTION | OBSERVATION | GUIDANCE
- currentValues: 유형별 현재 값 객체
- basis: 근거 배열

근거 필드:
- basisId: care_item_basis.id에 대응하는 UUID
- kind: SOURCE_RECORD | DIRECT_INPUT
- text: 해당 근거의 실제 내용

공통 규칙:
- v2는 currentValues와 basis를 사용한다. v1 summary 문자열로 대신하지 않는다.
- 필드 이름은 camelCase이며, 알 수 없는 필드와 잘못된 타입은 거부한다.
- UUID는 문자열 표현이 아니라 UUID 값으로 비교한다.
- version에 문자열·소수·불리언을 허용하지 않는다.
- 후보 ID와 후보 내 근거 ID는 중복되지 않아야 한다.
- BE는 해당 공간의 유효한 후보와 근거만 전달한다.
- AI는 요청에 없는 항목·근거·담당자를 만들어 참조하지 않는다.
- 상대 날짜는 원본 recordedAt과 Asia/Seoul을 기준으로 해석한다.
- 분석 시각이나 재시도 시각으로 상대 날짜를 다시 계산하지 않는다.
- 없는 날짜·시간·장소·담당자·사실을 추측해 채우지 않는다.

SOURCE_RECORD.text:
- 저장된 유효한 원본 근거 구절을 전달한다.
- 임의로 새 요약을 만들어 원본 근거처럼 전달하지 않는다.

DIRECT_INPUT.text:
- 독립적으로 저장된 직접 입력 근거 문서를 안정된 JSON 문자열로 직렬화한다.
- 현재 항목 문서와 원래 직접 입력 근거 문서를 임의로 동일시하지 않는다.
- AI는 이 문자열을 본문 동일성 비교 과정에서 다시 JSON으로 파싱하지 않는다.

basisId의 연결 대상은 안정적으로 유지한다.
같은 ID를 다른 항목·문서·출처에 재사용하지 않는다.
근거를 교체한다면 새 문서·연결 식별자로 구분한다.


## 4. currentValues의 정확한 형태

아래 유형별 키를 모두 전달한다.
nullable 값도 키를 생략하지 않고 null로 표현한다.
이는 내부 전송 형식이며 제품 입력 필수 조건을 새로 정하는 것이 아니다.

| 유형 | 키 목록 |
| --- | --- |
| SCHEDULE | title, description, status, scheduledDate, scheduledTime, location |
| TASK | title, description, status, dueDate, assigneeMembershipId |
| QUESTION | title, description, status, answerText |
| OBSERVATION | title, description, observedDate, observedTimeText |
| GUIDANCE | title, description, sourceName, receivedDate |

값 규칙:
- 일반 텍스트는 문자열 또는 null.
- 날짜는 실제 존재하는 YYYY-MM-DD 또는 null.
- scheduledTime은 유효한 HH:mm 또는 null.
- observedTimeText는 원문에 있는 시각 표현의 문자열 또는 null.
- assigneeMembershipId는 기존 담당자 UUID 또는 null.
- 숫자 등을 문자열로 자동 변환하지 않는다.
- 다른 유형의 필드, 알 수 없는 필드, 잘못된 날짜·시각은 거부한다.

상태:
- SCHEDULE: SCHEDULED | COMPLETED | CANCELED
- TASK: NOT_STARTED | IN_PROGRESS | COMPLETED
- QUESTION: UNCONFIRMED | CONFIRMED
- 위 세 유형의 status는 필수이며 null을 허용하지 않는다.
- OBSERVATION·GUIDANCE에는 status 키가 없다.
- 잘못된 상태를 기본 상태나 null로 보정하지 않는다.

질문 답변:
- currentValues.answerText는 BE가 제공하는 기존 확정 답변이다.
- 응답의 changes.answerText는 AI가 새로 제안하는 미확정 답변이다.
- 미확정 제안을 currentValues에 섞지 않는다.
- answerText: null은 답변이 없거나 현재 연동에서 제공되지 않았음을 의미한다.
- null만으로 기존 확정 답변이 없다고 단정하지 않는다.
- 제공되지 않은 답변과의 일치·상충을 추측하지 않는다.

현재 BE에는 확정 답변 저장·조회가 아직 연결되지 않았다.
따라서 v2 최초 구현에서는 currentValues.answerText를 null로 제공한다.
CONFIRMED + answerText: null도 현재 제공 범위에서는 유효하다.
상태는 확인됐지만 답변 본문이 제공되지 않은 것으로 해석한다.

향후 실제 확정 답변이 연결되면 값을 제공한다.
답변 목록의 첫 번째 값·최근 값이나 AI 제안을 임의로 확정 답변으로 선택하지 않는다.
확정 답변 수정·질문 재개 정책까지 이번 계약으로 확정하는 것은 아니다.


## 5. v2 응답

루트:
- requestId
- suggestions

각 suggestion에는 다음 키가 모두 있어야 한다.
nullable 값도 키를 생략하지 않는다.

- operation
- type
- targetItemId
- expectedVersion
- candidateItemIds
- title
- evidence
- changes
- clearFields
- relationKind
- reviewReasons
- basisReferences

기본 표현:
- 대상 없음: null
- 배열 없음: []
- 변경분 없음: {}

operation:
- CREATE
- UPDATE
- RESOLVE
- LINK_EVIDENCE

relationKind:
- NEW
- RELATED
- DUPLICATE
- CONFLICT
- UNDETERMINED

reviewReasons:
- TARGET_AMBIGUOUS
- TARGET_NOT_FOUND
- VALUE_UNCLEAR
- CONFLICT
- ASSIGNEE_UNRESOLVED

reviewReasons와 ID 목록에는 중복을 허용하지 않는다.
모든 제안은 사용자 검토 대상이며, reviewReasons가 비어 있어도 자동 반영하지 않는다.

evidence:
- 이번 요청 content에 실제 존재하는 연속된 구절이어야 한다.
- 공백뿐인 값은 허용하지 않는다.
- 기존 후보의 근거를 이번 소식의 evidence로 대신하지 않는다.

title:
- 제안 목록에 표시할 설명 제목이다.
- 관리 항목의 실제 제목 변경은 changes.title이다.
- 두 필드를 자동으로 같은 값으로 취급하지 않는다.


## 6. 작업·관계·대상 조합

| operation | 허용 유형 | relationKind |
| --- | --- | --- |
| CREATE | 모든 유형 | NEW |
| UPDATE | 모든 유형 | RELATED, CONFLICT, UNDETERMINED |
| RESOLVE | QUESTION만 | RELATED, CONFLICT, UNDETERMINED |
| LINK_EVIDENCE | 모든 유형 | DUPLICATE, UNDETERMINED |

CREATE:
- targetItemId = null
- expectedVersion = null
- candidateItemIds = []
- TARGET_AMBIGUOUS·TARGET_NOT_FOUND를 붙이지 않는다.

대상이 확실한 기존 항목 제안:
- targetItemId는 요청 후보 중 하나다.
- expectedVersion은 그 후보의 version과 정확히 같아야 한다.
- 제안 type은 대상 후보의 type과 같아야 한다.
- candidateItemIds = []
- 대상 관련 두 검토 사유를 붙이지 않는다.

대상이 모호한 제안:
- targetItemId = null
- expectedVersion = null
- candidateItemIds에 같은 유형의 요청 후보를 1개 이상 제시한다.
- TARGET_AMBIGUOUS가 필요하다.

대상을 찾지 못한 제안:
- targetItemId = null
- expectedVersion = null
- candidateItemIds = []
- TARGET_NOT_FOUND가 필요하다.

추가 규칙:
- TARGET_AMBIGUOUS와 TARGET_NOT_FOUND는 함께 사용하지 않는다.
- UNDETERMINED에는 검토 사유가 최소 하나 필요하다.
- CONFLICT 관계에는 CONFLICT 검토 사유와 비교 가능한 기존 근거가 필요하다.
- 기존 비교 근거가 없는데 상충이라고 단정하지 않는다.
- ASSIGNEE_UNRESOLVED는 TASK에만 사용한다.
- 대상이 모호하거나 없다는 이유로 정상적인 검토 제안을 오류나 빈 결과로 바꾸지 않는다.


## 7. changes와 clearFields

유형별 변경 필드:
- 공통: title, description
- SCHEDULE: scheduledDate, scheduledTime, location, status
- TASK: dueDate, status
- QUESTION의 RESOLVE: answerText
- OBSERVATION: observedDate, observedTimeText
- GUIDANCE: sourceName, receivedDate

담당자 관련 예외:
- currentValues.assigneeMembershipId는 기존 정보로 입력할 수 있다.
- AI 응답의 changes.assigneeMembershipId는 금지한다.
- clearFields의 assigneeMembershipId도 금지한다.
- CREATE에서는 담당자를 자동 지정하지 않는다.
- UPDATE에서는 현재 담당자를 유지한다.
- 담당자 지정·변경·해제 판단이 필요하면 ASSIGNEE_UNRESOLVED를 사용한다.
- 이후 사람이 담당자를 선택하는 기능과 이 AI 출력 제한은 별개다.

changes:
- 유형별 허용 필드만 사용한다.
- 값은 문자열이며 날짜·시각·상태 형식을 검증한다.
- UPDATE에서 생략한 필드는 유지한다.
- CREATE에서 생략한 값은 미상이며 임의 기본값으로 채우지 않는다.
- null이나 빈 문자열을 삭제 명령으로 사용하지 않는다.
- 언급되지 않은 값을 지우거나 변경하지 않는다.

clearFields:
- UPDATE에서만 사용한다.
- 명시적인 값 제거 제안을 필드 이름으로 표현한다.
- 중복 필드와 changes에 동시에 존재하는 필드는 거부한다.
- 다른 유형의 필드나 허용되지 않은 필드를 넣지 않는다.
- 실제 삭제 가능 여부와 제품 필수값은 BE의 최종 반영 검증을 따른다.
- AI가 삭제를 제안했다고 필수값 제거가 허용되는 것은 아니다.
- 미정인 제품 필수값·필드 길이·삭제 정책을 새로 확정하지 않는다.

RESOLVE:
- changes.answerText만 사용한다.
- answerText는 누락·null·빈 문자열·공백뿐인 값을 허용하지 않는다.
- 루트 answerText 필드를 추가하지 않는다.
- clearFields = []
- 질문 제목·설명 변경을 같은 RESOLVE에 섞지 않는다.
- 제안 저장만으로 답변 확정이나 CONFIRMED 전환을 수행하지 않는다.

LINK_EVIDENCE:
- changes = {}
- clearFields = []
- 기존 값을 변경하는 작업으로 취급하지 않는다.
- 실제 근거 연결은 사용자 최종 반영 단계에서 BE가 처리한다.

상태 값이 유효한 enum이라는 사실만으로 모든 상태 전이가 허용되는 것은 아니다.
미정인 일정 되돌리기·확인된 질문 재개 정책을 임의로 추가하지 않는다.
날짜가 지났다는 이유만으로 완료를 제안하지 않는다.


## 8. 빈 UPDATE의 예외

changes = {}, clearFields = []인 UPDATE는 다음 중 하나가 있을 때만 허용한다.

- ASSIGNEE_UNRESOLVED
- VALUE_UNCLEAR

다른 대상·관계·유형·근거 규칙도 모두 만족해야 한다.

허용 예:
- 담당자 변경만 언급되어 사람이 판단해야 하는 TASK.
- 변경 의도는 있으나 새 값이 불명확한 항목.

거부 예:
- 변경분도 없고 검토 사유도 없음.
- 변경분이 없고 TARGET_AMBIGUOUS 또는 TARGET_NOT_FOUND만 있음.

빈 UPDATE를 피하려고 description 등 불필요한 변경을 만들지 않는다.
이 예외를 RESOLVE에 적용하지 않는다.

빈 UPDATE는 검토용 제안이다.
사람이 실제 변경 내용을 보완하기 전에는 변경 반영 완료로 처리하지 않는다.
검토 사유 자체가 현재 값이나 담당자를 변경하지 않는다.


## 9. basisReferences

각 원소:
{
  "itemId": "<요청 후보 UUID>",
  "basisIds": ["<해당 후보의 basisId>"]
}

공통 검증:
- 같은 itemId는 한 번만 등장한다.
- 각 basisIds는 비어 있지 않고 중복이 없어야 한다.
- 요청에 포함된 후보와 그 후보에 속한 basis만 참조한다.
- 없는 basisId나 다른 후보 소속의 basisId는 거부한다.
- UUID 대소문자 차이는 동일 값으로 처리한다.
- 실제 비교에 사용한 근거를 반환한다.

최소 참조:
- 명확한 UPDATE·RESOLVE·LINK_EVIDENCE:
  선택 대상의 근거를 최소 하나 참조한다.
- TARGET_AMBIGUOUS:
  candidateItemIds의 각 후보에 대해 근거를 최소 하나씩 참조한다.
- 모든 근거를 나열할 필요는 없다.
- 다른 후보의 근거로 선택 대상의 필수 근거를 대신할 수 없다.

추가 비교 참조:
- 필수 참조 외에 실제 비교한 다른 요청 후보의 근거를 포함할 수 있다.
- CREATE는 빈 참조를 허용한다.
- CREATE도 기존 후보와 비교한 뒤 NEW로 판단했다면 비교 근거를 포함할 수 있다.
- 이때도 CREATE의 대상·버전·후보 목록 규칙은 그대로 유지한다.
- TARGET_NOT_FOUND도 빈 참조를 허용한다.
- 비교 후 제외한 후보가 있다면 그 비교 근거를 포함할 수 있다.

CONFLICT:
- 이번 원문의 evidence와 비교 가능한 기존 basisReferences가 모두 필요하다.

basisReferences는 비교 근거 설명이다.
변경 대상 지정, 권한 부여, 자동 근거 연결 명령으로 취급하지 않는다.

근거 존재·소속·필수 참조는 계약 테스트로 검증한다.
근거가 의미상 적절한지는 별도 모델 품질 평가로 검증한다.


## 10. requestId, 재시도, 수신 기록

- FE의 clientRequestId와 내부 AI requestId는 역할이 다르다.
- BE는 분석 실행마다 requestId를 발급한다.
- 사용자 재시도는 새 실행·새 requestId를 사용한다.
- 재시도에도 원본 recordedAt을 유지한다.
- 새 실행의 후보와 계약 버전은 그 실행에 맞게 구성할 수 있다.
- 같은 requestId로 다른 후보·본문을 다시 보내지 않는다.

AI 처리:
1) 인증 및 요청 검증
2) 동일 요청 비교 및 원자적 수신 기록 등록
3) 등록에 성공한 최초 요청만 제공자 호출

기존 수신 기록의 실행 상태와 관계없이:
- 같은 ID·같은 본문 → 409 REQUEST_ALREADY_RECEIVED
- 같은 ID·다른 본문 → 409 REQUEST_ID_REUSED

수신 기록 저장·확인이 불가능하면:
- 503 RECEIPT_STORE_UNAVAILABLE
- 제공자 호출 0회

동시 수신:
- 최초 등록에 성공한 요청만 제공자를 호출한다.
- 애플리케이션 메모리의 단순 조회 후 삽입으로 구현하지 않는다.
- 여러 프로세스와 재시작 후에도 보존된 기록을 기준으로 차단한다.

추가 원칙:
- AI 분석 결과 캐시는 만들지 않는다.
- 원문·분석 결과·인증키를 수신 기록에 저장하지 않는다.
- 모델 SDK 자동 재시도를 활성화하지 않는다.
- 등록 이후 통신이 끊기거나 결과가 유실돼도 같은 ID로 모델을 자동 재실행하지 않는다.
- 명시적인 새 실행은 추가 모델 호출·비용이 발생할 수 있다.
- 네트워크 경계의 정확히 한 번 실행·과금을 보장한다고 표현하지 않는다.
- 시간 초과가 제공자의 실제 처리·과금 취소를 보장하지 않는다.
- 저장소 종류·보관 기간·정리 정책은 미정이다.
- 기록 정리 이후까지 영구 중복 차단된다고 표현하지 않는다.


## 11. 동일 본문 판정

검증을 통과한 요청을 기준으로 비교한다.

같은 본문으로 취급:
- 외부 JSON 객체 키 순서 차이
- 디코딩된 문자열은 같고 JSON 이스케이프 표현만 다른 경우
- UUID 필드의 영문 대소문자 차이
- recordedAt이 같은 시점을 나타내는 오프셋 표현 차이

다른 본문으로 취급:
- content·basis.text의 공백·줄바꿈·실제 문자 차이
- 후보·근거 배열 순서 차이
- 후보 버전·필드 값·근거 차이
- DIRECT_INPUT.text 내부 JSON의 공백·키 순서 차이

정규화:
- 객체 키를 재귀적으로 정렬한다.
- 배열 순서는 보존한다.
- trim이나 유니코드 정규화를 수행하지 않는다.
- UUID로 선언된 필드만 소문자·하이픈 표현으로 정규화한다.
- 문자열 안의 UUID처럼 보이는 텍스트는 변경하지 않는다.
- DIRECT_INPUT.text는 불투명한 문자열로 비교한다.
- 동일 JSON 객체의 중복 키는 일반 객체로 변환되기 전에 거부한다.
- 정규화한 JSON을 안정된 UTF-8 표현으로 직렬화하여 HMAC 비교값을 만든다.

시간:
- recordedAt은 초와 시간대 오프셋이 있는 ISO 8601 문자열이다.
- 소수초는 생략하거나 1~6자리를 허용한다.
- 비교용 표현은 UTC·소수초 6자리·Z로 고정한다.
- 7자리 이상은 끝자리가 0이어도 거부한다.
- 초과 정밀도를 절삭·반올림하지 않는다.
- 파서가 정밀도를 줄이기 전에 원래 문자열을 검사한다.
- 시간대 없는 값이나 숫자 타임스탬프는 거부한다.

동일 시점 예:
2026-10-16T09:00:00.123456+09:00
2026-10-16T00:00:00.123456Z

BE는 저장된 원본 시각을 사용하고,
직접 입력 JSON 및 후보·근거 순서를 안정적으로 생성한다.

수신 기록은 계약 버전과 requestId로 식별한다.
정규화 규칙 버전과 HMAC 키 버전은 비교용 메타데이터로 관리한다.
규칙·키 교체 때문에 기존 요청을 새 요청으로 취급하지 않는다.
기존 기록을 안전하게 비교할 수 없으면 모델 호출을 차단한다.

비교용 HMAC 키를 양쪽에 공유할 필요는 없다.
AI의 수신 기록 비교 책임에 맞춰 관리하며,
인증키나 BE의 기존 COMMAND_DIGEST_KEY를 자동으로 재사용하지 않는다.


## 12. 실행별 계약 버전과 BE 저장

- BE는 분석 실행을 접수할 때 계약 버전을 고정한다.
- 대기·실행 중 활성 버전 설정이 바뀌어도 해당 실행 버전은 바뀌지 않는다.
- 명시적인 재시도는 새 실행이므로 현재 활성 버전을 사용한다.
- 기존 v1 실행·저장 데이터는 계속 해석할 수 있어야 한다.
- 남은 v1 실행을 처리할 수 있는 동안 v1 지원을 유지한다.

BE는 응답을 독립 검증한다.
AI가 검증했다고 가정하고 그대로 저장하지 않는다.

v2 구현 목표:
- CREATE·UPDATE·RESOLVE·LINK_EVIDENCE를 검토 제안으로 저장한다.
- 대상·버전·후보·관계·검토 사유·변경분·삭제 제안·비교 근거를 보존한다.
- 여러 제안 중 하나라도 계약 위반이면 전체 결과를 거부한다.
- 저장 중 실패하면 부분 저장 없이 전체 롤백한다.
- 정상적인 suggestions: []는 성공이며 제안 0건으로 처리한다.

현재 BE가 non-CREATE 응답을 ANALYSIS_DEPENDENCY_UNAVAILABLE로
처리하는 것은 기존 구현의 제한이다.
v2 지원이 완료된 것으로 가정하지 말고, v2 저장 구현에서 해소해야 한다.

분석 완료 시:
- 현재 공간·요청자 참여 상태를 재검증한다.
- 원본과 실제 전달한 모든 후보·문서·근거 연결·출처·버전을 재검증한다.
- 응답 basisReferences만 골라 검증하지 않는다.
- 분석 중 입력이 무효화되면 늦은 결과를 저장하지 않는다.
- basisId는 연결 행인 care_item_basis.id다. 연결 해제는 해당 행 제거를 뜻한다.
- 연결 해제 시 SOURCE_RECORD의 원본 소식·evidence, DIRECT_INPUT의 원본 content_document는 보존한다. 원본 내용이 남아 있어도 연결 행이 없으면 유효한 입력 근거로 보지 않는다.
- DIRECT_INPUT은 현재 항목 문서와 별개인 최초 직접 입력 문서도 재검증한다. 응답에서 참조하지 않은 입력 근거에도 같은 기준을 적용한다.

조회와 삭제:
- 저장 후 근거가 무효화된 결과를 정상 검토 제안으로 노출하지 않는다.
- 삭제 시 답변·변경분·비교 내용 등 새로 저장하는 파생 내용도 제거 대상이다.
- 원본 삭제 전체 API나 파생 내용 제거가 이미 구현됐다고 가정하지 않는다.

공개 조회:
- 삭제된 분석 원본 소식의 분석 상세 조회는 기존 404 RESOURCE_NOT_FOUND 정책을 유지한다. 내부 실행 상태와 공개 조회 HTTP 상태는 별도로 검증한다.
- 원본이 활성 상태인 분석의 조회는 권한 있는 활성 구성원을 기준으로 한다. 요청자 참여 종료 사례의 조회자는 다른 활성 구성원이다.
- AI 변경분을 내부 DTO 그대로 노출하지 않는다.
- 현재 값과 제안 변경분, 삭제 필드, 미확정 답변을 구분한다.
- 현재 버전이 분석 기준 버전과 달라졌다면 최신 값에 제안을 임의 병합하지 않는다.
- 대상이 불명확한 경우 확정된 대상의 현재 값·병합 결과가 있는 것처럼 표시하지 않는다.

제안 저장과 최종 반영은 별개다.
최종 반영에서는 소유자 권한·구성원 예외·잠금·버전·선택 묶음의
전체 성공/전체 실패를 BE가 다시 검증한다.


## 13. AI 오류와 BE 실패 코드

아래는 v2-20261021.1의 전체 HTTP/code 허용 목록이며 16.3절의 추가 합의를 포함한다.
기계 판독 기준은 공통 fixture의 error-mapping.json이며, 발생 조건·우선순위는 16절을 따른다.
현재 BE에 모든 실패 코드가 이미 추가됐다는 뜻은 아니다.

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

- BE는 허용한 HTTP 상태·오류 코드 조합만 매핑한다.
- 알 수 없는 응답·잘못된 오류 본문은 안전한 일반 실패로 처리한다.
- 제공자의 원본 message를 저장하거나 공개 응답에 그대로 전달하지 않는다.
- 정상 요청에 대한 제공자 결과가 계약 위반이면 AI는 502 INVALID_AI_RESPONSE를 반환한다.
- 요청 본문의 필드·개수·문자열 합계 위반은 422 INVALID_REQUEST이며, 전송 바이트 초과는 413 REQUEST_TOO_LARGE다.
- 정상적인 모호함·대상 없음은 200의 검토 제안이다.
- BE의 비동기 상태 조회는 활성 원본에 접근 권한이 있는 구성원의 정상 조회 시 200이다. 원본 삭제 시 분석 상세 조회는 404 RESOURCE_NOT_FOUND이며 내부 AI HTTP 상태와 구분한다.
- 내부 AI 오류가 이미 완료된 분석 접수 HTTP 응답을 바꾸지는 않는다.
- 오류 코드 분리만으로 자동 재시도나 재시도 권한을 새로 허용하지 않는다.


## 14. 공통 fixture 구성과 판정

실제 모델 결과가 아닌 합성 계약 예시를 사용한다.
같은 사례와 기대값을 두 레포에서 공유하며 임의로 다르게 수정하지 않는다.

각 사례:
- caseId
- 검증 목적
- 계약 버전
- 요청 헤더 및 본문
- 가짜 제공자 응답
- 기대 AI HTTP 상태·응답 또는 오류 코드
- 기대 제공자 호출 횟수
- 기대 BE 실행 상태·실패 코드·저장 결과
- 필요한 경우 분석 전후 BE 상태 변경 조건

원문 JSON이 중요한 사례는 객체로 변환된 데이터만 저장하지 않는다.
중복 키·이스케이프·시간 정밀도 검증용 실제 전송 문자열을 보존한다.

A. 정적 계약

필수 정상 사례:
- 다섯 유형의 완전한 currentValues.
- nullable 필드의 명시적 null.
- QUESTION의 기존 확정 답변과 새 제안 답변 분리.
- CONFIRMED + answerText: null.
- 기존 담당자는 입력에 존재하지만 AI 변경분에는 없음.
- 담당자 지정·변경·해제만 언급한 빈 UPDATE + ASSIGNEE_UNRESOLVED.
- 새 값이 불분명한 빈 UPDATE + VALUE_UNCLEAR.
- 명확한 대상과 그 대상의 근거 참조.
- 모호한 후보 각각의 근거 참조.
- 비교 없는 CREATE와 비교 근거가 있는 CREATE.
- 다른 변경분 규칙도 만족하는 TARGET_NOT_FOUND + 빈 참조.
- 정상적인 빈 suggestions.

필수 무효 요청:
- 필수 키 누락·알 수 없는 키·다른 유형의 키.
- 잘못된 타입·날짜·시각·상태.
- 중복 JSON 키.
- 지원 범위를 초과한 recordedAt 정밀도.
- 기대값: 422 INVALID_REQUEST, 제공자 호출 0회.
- 인증·헤더 ID 오류는 별도의 401·400 계약을 따른다.

필수 무효 제공자 결과:
- 허용 사유 없는 빈 UPDATE.
- 대상 관련 사유만 있는 빈 UPDATE.
- changes 또는 clearFields를 통한 담당자 설정·해제.
- RESOLVE 답변 누락·null·빈 문자열·공백뿐인 값.
- 대상 ID·버전·유형 불일치.
- 필수 대상 근거 참조 누락.
- 없는 근거·다른 후보 소속 근거·중복 참조·빈 basisIds.
- 기존 비교 근거 없는 CONFLICT.
- 원문에 없는 evidence.
- 기대값: 502 INVALID_AI_RESPONSE.
- 가짜 제공자 호출 1회, BE 제안 저장 0건.

유효·무효 제안이 섞인 응답은 전체 실패하는 사례를 포함한다.
일부 정상 제안만 저장하면 안 된다.

B. AI 수신 기록

- 같은 ID·같은 본문 → 409 REQUEST_ALREADY_RECEIVED, 추가 호출 0회.
- 같은 ID·다른 본문 → 409 REQUEST_ID_REUSED, 추가 호출 0회.
- 기록 저장·확인 불가 → 503 RECEIPT_STORE_UNAVAILABLE, 호출 0회.
- 동시 수신 → 최초 등록 성공 요청만 호출, 전체 최대 1회.
- 재시작 후 보존된 기록 재수신 → 동일한 차단.
- 실행 성공·실패·진행 중 여부와 관계없는 재수신 차단.
- 객체 키 순서·이스케이프·UUID 대소문자·동일 시점 오프셋 → 동일.
- 문자열 공백·줄바꿈·문자·배열 순서·값·버전·근거 변경 → 다름.
- DIRECT_INPUT.text 내부 JSON 표현 변경 → 다름.
- 소수초 6자리 허용·7자리 거부.
- 정규화/HMAC 키 버전 변경으로 중복 차단이 우회되지 않음.

C. BE 통합

- 정상 응답 및 유효한 입력 → COMPLETED, 검토 제안 저장.
- 완료 전에 원본 삭제·근거 연결 해제·후보 버전 변경
  → DISCARDED, 결과 저장 안 함.
- 원본 소식·evidence 또는 직접 입력 문서를 보존하고 care_item_basis 연결 행만 제거한 경우도 포함.
- DIRECT_INPUT의 연결 해제·원본 문서 무효화를 완료 전·저장 후 각각 검증한다. 응답이 참조한 근거와 참조하지 않은 입력 근거 모두 포함한다.
- 직접 입력 사례는 현재 항목 문서와 최초 직접 입력 문서를 서로 다른 ID로 준비하고, 현재 문서·후보 버전은 유지한 채 원본 근거만 무효화한다.
- 저장 완료 후 입력 근거 무효화 → 정상 제안·근거로 노출하지 않음.
- 완료 전 원본 삭제는 내부 DISCARDED·저장 0건과 공개 조회 404를 별도로 검증한다. 저장 후 원본 삭제는 최초 완료 상태·저장 건수와 삭제 후 공개 조회 404·파생 내용 제거를 구분하며 삭제 후 내부 상태 enum을 새로 고정하지 않는다.
- 유효한 입력이지만 요청자 참여 종료
  → FAILED / ANALYSIS_REQUESTER_INACTIVE.
- 제안 저장 중간 실패
  → 전체 롤백, FAILED / ANALYSIS_STORAGE_FAILED.
- 잘못된 결과를 AI가 200으로 보내는 경우도 BE가 독립 거부.
- 접수 후 활성 계약 버전 변경 → 실행별 고정 버전 유지.
- 명시적 재시도 → 새 실행·새 ID, 원본 recordedAt 유지.
- v1 회귀 테스트 유지.

이 fixture 통과는 계약 준수 검증이다.
실제 모델의 정확도·근거 적절성·환각 억제 품질을 증명하지 않는다.


## 15. 미정 사항과 구현 준비 완료 기준

초기 로컬·통합의 크기·개수 제한 및 오류는 16절에서 확정했다. 아래 규모 관련 미정은 운영 상한·선별 정책을 뜻한다.

임의로 확정하지 않을 사항:
- 운영 환경의 전체 크기·근거별 크기·근거 개수 상한 조정
- 후보·근거 선별 및 축약 기준
- 수신 기록 저장소·보관 기간·정리 및 키 교체 운영 절차
- 실제 모델·제공자·비용·품질 기준
- 운영 동시 실행·실행 중단 판정 등 운영값
- 제품 입력 필수값·길이·삭제 가능 필드의 미정 정책
- 일정 되돌리기·확인된 질문 수정 및 재개 정책
- 확정 답변 저장·조회와 currentValues의 실제 연결

기존 v1 제한을 임의로 변경하지 않는다.
v1 summary의 제한을 v2 currentValues·basis 전체에 그대로 적용하지 않는다.
운영 제한이 미정이라고 원문·근거를 조용히 잘라내거나 요약해 보내지 않는다.

현재 타임아웃 기준:
- AI 기본 20초, 최대 25초
- BE 연결 3초, 응답 30초
- 별도 측정·합의 전 임의 변경하지 않는다.
- SDK 자동 재시도는 활성화하지 않는다.

구현 준비 단계에서는:
1) 이 계약과 실제 양쪽 DTO·저장 구조의 차이를 확인한다.
2) 공통 fixture의 사례와 기대값을 맞춘다.
3) 필요한 BE·AI 변경 범위와 의존성을 구분한다.
4) 운영값 미정과 제품 정책 미정을 별도로 남긴다.

이후 별도 구현 요청에 따라 개발하고,
양쪽 계약 테스트·BE 상태 변경 테스트·v1 회귀 검증을 통과한 뒤
신규 분석을 v2로 전환한다.

## 16. 초기 로컬·통합 연동 합의 — v2-20261021.1

2026-10-21 BE 보완안과 사용자의 수용 지시를 반영한다. 계약·fixture revision은
`v2-20261021.1`이며 wire 계약 버전과 엔드포인트는 `v2`, `POST /v2/analyses`를 유지한다.
요청 DTO에 revision·제한 설정 필드를 추가하지 않는다. 이 절은 합의한 규격이며,
구현·검증 상태는 문서 상단의 현재 AI 구현과 테스트 범위를 따른다.

### 16.1 공통 상한과 계산

상한과 같으면 허용하고 초과하면 거부한다. 아래 제한을 모두 동시에 만족해야 한다.
기계 판독 기준은 fixture의 `limits.json`이다. 운영 성능·실제 모델 처리 가능량을 보장하지 않는다.

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

- 바이트는 비압축 UTF-8 JSON 본문 원문이며 공백·이스케이프를 포함하고 HTTP 헤더는 제외한다.
- 문자열은 JSON 디코딩 후, 타입 변환·UUID 정규화·날짜 정규화 **전**의 UTF-16 코드 단위다.
  Java String.length()에 맞추며 Python 일반 len()을 같은 의미로 사용하지 않는다.
- 전체 합계에는 모든 문자열 값과 배열의 문자열 원소를 재귀 집계한다. 키 이름·null·숫자·boolean은
  제외한다. 같은 문자열·ID도 등장 횟수대로 센다. trim·유니코드 정규화는 하지 않는다.
- DIRECT_INPUT.text는 문자열 전체로 집계하며 내부 JSON을 다시 해석하지 않는다.
- 자유 서술 필드는 title, description, location, observedTimeText, sourceName, answerText 중
  해당 유형·작업에서 허용하는 필드다. suggestion.title의 500과 changes.title의 10,000은 별개다.
  기존 필수 키·null·빈 문자열·날짜·상태·작업 조합 규칙을 유지한다.
- 참조는 실제 입력 후보와 해당 후보의 근거만 허용한다. 기존 중복 금지·필수 참조 규칙도 유지한다.
  다른 제안의 같은 근거 재참조는 허용하되 전체 문자열 합계에는 매번 포함한다.
- AI는 제공자 원문과 최종 전송 JSON도 각각 검증한다. 타입 변환 후 직렬화로 길이가 달라져도
  실제 전송하는 본문이 상한을 넘지 않아야 한다. BE는 받은 원문을 독립 검증한다.

### 16.2 저장 정책과 분석 범위

소식 content 10,000은 현재 BE 저장 정책과 일치한다. 일정 제목·설명·장소 및 공간 항목 수는
공개 저장 API에 같은 상한이 없으므로 저장 가능한 데이터가 분석 상한을 넘을 수 있다.
공개 저장 제한은 바꾸지 않는다. 원본 저장을 유지하고 분석만 실패 처리한다.
후보·근거 일부 제외, 절삭, 요약, 빈 제안 성공 처리를 하지 않는다. 후보 선별·운영 상한은 별도 합의다.
BE는 후보·근거를 구성하는 단계부터 개수·길이·누적 크기를 제한하며 전체 데이터를 무제한으로
메모리에 올린 뒤 검사하지 않는다. AI도 전체 본문/SDK 결과를 받은 뒤의 검사만으로 수신 보호를
대체하지 않는다. 미지원 압축을 자동 해제해 바이트 제한을 우회하지 않는다.

### 16.3 오류 매핑

이번 revision에서 추가한 세 조합은 다음과 같으며 13절의 전체 허용 목록에도 반영했다.
HTTP와 code를 쌍으로 확인한다.

| AI HTTP / code | BE 통신 어댑터 실패 코드 |
| --- | --- |
| 413 REQUEST_TOO_LARGE | AI_INTEGRATION_ERROR |
| 503 REQUEST_COMPARISON_UNAVAILABLE | AI_RECEIPT_STORE_UNAVAILABLE |
| 503 AI_OVERLOADED | AI_SERVICE_UNAVAILABLE |

- 요청 전송 바이트 초과는 413, 요청 필드·개수·문자열 합계 초과는 기존 422 INVALID_REQUEST다.
- 제공자 결과의 크기·개수·계약 위반은 기존 502 INVALID_AI_RESPONSE → AI_INVALID_RESPONSE다.
- BE의 전송 전 요청 제한 초과는 HTTP 호출 없이 AI_INTEGRATION_ERROR다.
- BE 응답 읽기 중 바이트 초과는 HTTP 상태와 무관하게 AI_INVALID_RESPONSE다.
- 미등록/잘못된 HTTP·code 조합, 잘못된 오류 본문은 AI_SERVICE_UNAVAILABLE로 처리한다.
- 오류 본문은 기존 code/message 형식과 안전한 고정 메시지를 사용한다. 원문·키·제공자 메시지를
  노출하지 않는다. BE 실행의 상태·실패 코드 저장은 별도 실행/저장 연결에서 명시적으로 매핑한다.
  이미 성공한 소식 저장을 실패로 뒤집지 않는다.

### 16.4 기존 기록 우선과 원자적 등록

인증·요청 검증을 통과한 요청에 다음 순서를 적용한다.

1. 기존 수신 기록을 확인한다. 확인된 같은 ID·같은 본문은 409 REQUEST_ALREADY_RECEIVED,
   다른 본문은 409 REQUEST_ID_REUSED, 안전한 비교 불가는 503 REQUEST_COMPARISON_UNAVAILABLE다.
   이 판정은 제공자 슬롯 부족보다 우선한다. 저장소 확인 실패는 기존 503 RECEIPT_STORE_UNAVAILABLE다.
2. 신규 요청은 제공자 슬롯을 확보한다. 확보하지 못하면 503 AI_OVERLOADED로 거부한다.
3. 슬롯 확보 후 원자적 등록에서 다른 요청이 먼저 등록했는지 다시 확인한다. 기존 기록을 발견하면
   같은 비교 규칙으로 판정하고 사용하지 않은 슬롯을 해제한다. 최초 등록 성공만 제공자를 호출한다.
4. 과부하로 거부된 요청은 **그 요청이** 새 기록을 만들지 않고 호출하지 않는다. 경쟁 요청이 만든
   기록까지 0건이라고 가정하지 않는다. 판정/등록은 관측된 기록과 원자적 순서로 검증한다.
5. 등록 이후 실패·타임아웃·과대 결과·연결 취소에도 기록을 보존한다. 자동 삭제나 재등록하지 않는다.

본문 수신·파싱의 자원 보호는 제공자 슬롯과 별도로 구성한다. 인증·검증 전 수신 보호가
무한 대기/무제한 버퍼링을 허용한다는 의미가 아니다. 기존 기록 우선 시나리오는 인증·검증 통과 후
제공자 슬롯이 가득 찬 조건을 명시한다.

### 16.5 시간·슬롯·재시도와 AI 내부 책임

- 자동 재시도·v1 fallback은 없다. 명시적 재시도는 새 실행·새 requestId와 원본 recordedAt을 쓴다.
  크기 초과는 동일 입력 재전송으로 해결되지 않는다. 비교 불가를 자동 새 ID로 우회하지 않는다.
- Retry-After에 근거 없는 복구 시간을 넣지 않는다.
- 제공자 대기 기본 20초·최대 25초, BE 연결 3초·전체 HTTP 기한 30초를 유지한다.
  AI는 본문 수신·저장소 대기·검증·직렬화에 제한을 두고 전체 예산을 검증한다.
- 프로세스당 동시 제공자 처리 2개·대기열 없음은 초기 로컬·통합 설정이다. 프로세스/인스턴스가
  늘면 전체 동시 처리 수도 늘어난다. 운영값은 측정 후 결정한다.
- 취소를 억제하는 제공자를 포함해 실제 작업이 끝날 때까지 슬롯을 점유한다. 응답 타임아웃과
  제공자 작업 종료·과금 취소를 동일시하지 않는다.
- DB 경로·권한·잠금 대기·명시적 초기화, HMAC 전용 키·버전·교체, 수신/파싱 보호 수단,
  제공자별 토큰/응답 읽기 예산, 민감 정보 없는 진단은 AI 내부 구현 책임이다.
- 보관 기간·정리 이후 중복 차단 범위는 미정이다. 합의 전에 자동 삭제를 도입하지 않는다.

### 16.6 fixture와 구현 순서

이전 revision의 manifest 원문과 B-uncomparable-record 원본을 history/에 보존한다. 전체 이전
번들과 계약은 revision-history.json의 고정 Git 커밋에서 복원한다. 새 revision의 caseId 증감과
기대값 변경을 기록하고, 같은 revision의 파일을 BE가 그대로 복사한다. manifest 자체의 SHA-256은
자기 참조 없이 전달 시 별도로 계산한다.

새 L-*는 각 상한 -1/상한/+1, UTF-16·UTF-8·이스케이프·반복 문자열·정규화 전 집계를 검증한다.
독립 구성이 가능한 경계는 다른 조건을 만족한다. evidence 10,001자 및 입력 상한을 초과하는
참조 수처럼 독립 구성이 불가능한 사례는 coupledViolations에 동반 위반을 명시하며,
개별 제한의 단위 측정과 전체 결과 거부를 구분한다. 기대값을 구현 상태에 맞춰 약화하지 않는다.

새 H-*는 길이 헤더 없는 수신·오류 조합·중복과 과부하·등록 경쟁·취소/타임아웃 이후 슬롯 유지·
과대 결과 이후 기록 보존·부분 저장 및 자동 재호출 금지를 정의한다.
생성 규칙과 실제 원문 길이·SHA-256·디코딩 문자열 길이는 generated.json에 고정하고
GENERATION.md를 기준으로 양 언어에서 재현한다. fixture 계산 도구를 실제 서비스 검증기로 사용하지 않는다.

계약·fixture 검증 및 기준 커밋 전달 → BE 동기화 → 양쪽 HTTP/통신 구현·검증 → BE 실행·저장 연결
순서로 진행한다. v1 회귀는 계속 유지한다. 이전 무제한 허용 런타임 테스트는 이슈 #13에서
이 계약의 기대값으로 교체했다. 명세 검증·런타임 실행 검증·모델 품질 검증을 구분해 보고한다.
