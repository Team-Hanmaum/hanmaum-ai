# BE↔AI v2 공통 fixture

계약 기준: 2026-10-16 기본 계약 + 2026-10-21 초기 연동 합의, **v2-20261021.1**.
전체 계약은 AI 저장소의 `docs/ai-contract-v2.md` 16절까지이며 HTTP 런타임 구현은 후속이다.
현재 공유 revision은 `manifest.json`의 `revision`이다. AI가 원본을 관리하며 BE 복사·실행 검증 결과는 별도 확인한다.

이 디렉터리 전체를 BE의 `src/test/resources/ai-contract/v2/`에 복사한다.
`cases/`, `payloads/`, `history/`, `generated.json`, `limits.json`, `revision-history.json`,
`error-mapping.json`, `manifest.json`, 문서와 `.gitattributes`까지 manifest에 있는 전체 파일을 동일하게 관리한다.
Python 테스트와 manifest 도구는 AI 저장소에만 두며 BE는 같은 사례를 Java 테스트에서 읽는다.

## 무엇이 검증되는가

| 모음 | 소비할 테스트 | 현재 AI에서 실행하는 범위 |
| --- | --- | --- |
| `cases/static.json` | v2 HTTP 요청·제공자 결과 검증, BE 오류 매핑 | 파일 무결성·참조·기대값 일관성 |
| `cases/receipts.json` | 실제 수신 기록 어댑터의 순차·동시·재시작·키 교체 검증 | 시나리오 형식·호출 수·비교 사례 일관성 |
| `cases/backend.json` | BE의 DB 트랜잭션·권한·입력 의존성·조회 통합 테스트 | 상태 변경 시나리오와 데이터 참조 일관성 |
| `cases/limits.json` | 문자열·개수·누적·바이트 제한 및 계산 경계 | 생성 원문 길이·SHA-256·기대값 및 기존 조건 검증 |
| `cases/http.json` | 수신·슬롯·과부하·오류·취소·BE 독립 검증 | 시나리오 형식·참조·우선순위 기대값 검증 |

현재 v2 모델·기존 내부 수신 기록/SQLite는 구현했으며 v2 endpoint와 새 제한·슬롯·외부 오류는
후속이다. 기존 STATIC 139개와 RECEIPT 29개는 내부 코드로도 검증한다. B-uncomparable-record는
기존 내부 차단 회귀와 새 HTTP 기대값을 구분한다. 새 LIMITS/HTTP 검사는 명세의 일관성 검사이며
실제 HTTP·자원 보호·BE 통합 완료가 아니다. 기존 v1 회귀와 실제 모델 품질도 구분한다.

## 공통 데이터 형식

각 모음은 사례 배열이다. `caseId`는 전체 모음에서 유일하며 목적이 바뀌면 새 ID를 사용한다.

- `category`: `STATIC`, `RECEIPT`, `BACKEND`.
- `purpose`: 검증 목적. `contractSections`는 최종 계약의 관련 절 번호.
- `contractVersion`: 실행 계약 메타데이터. 요청 JSON에 새 필드를 추가하는 것이 아니다.
- `request`: `method`, `path`, `headers`, `bodyFile`. 파일 경로는 항상 이 디렉터리 기준이다.
- `provider`: `RETURN`이면 `bodyFile`의 JSON을 반환하는 가짜 제공자. `UNAVAILABLE`은 호출 중 예외, `TIMEOUT`은 완료하지 않는 호출, `DISABLED`는 제공자 미설정이다.
- `expected.ai`: HTTP 상태, 성공 `bodyFile` 또는 오류 `code`, `providerCalls`. `messagePolicy: NON_EMPTY_SANITIZED`는 비어 있지 않은 안전한 문자열만 요구하며 문구를 새로 고정하지 않는다.
- `expected.be`: 실행 상태·실패 코드·저장 건수 등 해당 사례에서 판정할 항목만 명시한다. 명시하지 않은 필드나 오류 메시지는 새 계약이 아니다.
- `publicQuery`: BACKEND 사례의 공개 분석 상세 조회 대상·시점·조회자. 내부 AI 요청인 `request`와 구분한다. `setup`, `events`, `publicQuery`, `expected.be`의 표식은 테스트 메타데이터이며 AI 요청 DTO나 BE 공개 응답에 추가할 필드가 아니다.
- 성공의 `bodyFile` 비교는 JSON 객체 기준이며 UUID는 값으로 비교한다. 원문 전송 바이트 자체를 성공 응답과 비교하는 것은 아니다.
- 입력 인증키 `fixture-only-not-a-secret`는 합성 테스트용 상수다. 환경 설정에도 같은 테스트 상수를 사용하고 운영 비밀값을 읽지 않는다.

파일을 `bodyFile`에서 읽은 UTF-8 **바이트 그대로** 전송한다. `json=...`, ObjectMapper read/write 등으로 요청을 재직렬화하면 중복 키·이스케이프·소수초 표현 사례가 사라질 수 있다. Python HTTPX는 `content=path.read_bytes()`, Java 클라이언트는 원문 byte[] 전송을 사용한다.

manifest의 SHA-256은 **파일 원문 바이트의 동기화 검사**다. 런타임의 동일 요청 판정용 HMAC이 아니며, 해시가 다른 파일도 정규화 후 동일 요청일 수 있다. `.gitattributes`는 checkout 시 LF를 유지한다. 파일 자동 정렬·재포맷·줄바꿈 변환을 피한다.

## 정적 사례 실행

1. 각 사례마다 격리된 수신 기록 저장소와 설정을 준비한다. 사례 사이의 같은 requestId가 다른 사례를 차단하지 않게 한다.
2. `provider`의 동작과 호출 횟수 계측을 설치하고 `request`를 전송한다.
3. 실제 HTTP·본문·오류·제공자 호출 횟수를 `expected.ai`와 비교한다.
4. BE 테스트에서는 같은 응답을 내부 HTTP 어댑터에 주입하고 `expected.be`를 확인한다. 정적·수신 기록 사례의 상태 조회 200은 원본이 활성 상태이고 조회자가 접근 권한이 있는 활성 구성원인 조건이다. 내부 AI 오류 상태와 별개다.

무효 요청도 정상 가짜 제공자를 설정한다. 호출 0회가 실제로 보장되는지 확인하기 위한 것으로, 가짜 제공자를 생략해 검증을 통과시키지 않는다. 무효 제공자 결과는 호출 1회·전체 결과 거부·BE 저장 0건이 기대값이다.

## 수신 기록 시나리오 실행

- `steps`는 배열 순서대로 실행한다. `SUBMIT`의 호출 수는 해당 요청의 추가 호출 수다. 최상위 `totalProviderCalls`는 시나리오 전체 수다.
- `seedReceipt`는 이전에 등록된 기록의 테스트 준비 조건이다. `executionState`의 SUCCEEDED·FAILED·IN_PROGRESS는 시나리오 용어이며 저장소 enum이나 스키마를 새로 정하지 않는다. 이 시드에 원문이 있는 것은 테스트 준비용이며 실제 수신 기록에 원문을 저장하라는 뜻이 아니다.
- `FAIL_RECEIPT_STORE`는 READ 또는 REGISTER 단계 실패를 주입한다. `RESTART_AI_PRESERVE_RECEIPTS`는 앱 인스턴스를 새로 만들되 저장소를 유지한다.
- `SUBMIT_CONCURRENT`는 같은 저장소를 공유하는 서로 다른 두 AI 인스턴스에 동시에 제출한다. 결과 순서는 무관하며 `aiResults`의 다중집합과 전체 호출 수 1을 확인한다. 순차 호출로 대체하지 않는다.
- `ROTATE_COMPARISON_METADATA`의 버전 문자열은 합성 표식이다. 실제 키·보관 기간·회전 정책을 정의하지 않는다. 이전 기록을 비교 가능한 경우 동일 요청은 차단한다.
- B-uncomparable-record는 이번 합의로 503 REQUEST_COMPARISON_UNAVAILABLE 및
  BE AI_RECEIPT_STORE_UNAVAILABLE를 고정했다. 이전 BLOCKED 원문은 history/에 보존한다.
  현재 내부 예외를 테스트에서 HTTP로 임의 변환해 구현 완료로 표시하지 않는다.
- `bodyEquality`는 정규화 후 동일 본문 판정의 기대값이다. SHA-256의 파일 동일성과 혼동하지 않는다.
- 개별 SUBMIT의 BE 기대값은 그 응답을 BE에 독립 주입했을 때의 매핑이다. 연속 단계가 같은 BE 실행의 상태를 덮어쓴다는 뜻이 아니다. 동시 수신 사례도 실제 실행 완료 결과를 중복 오류로 덮어쓰지 않는다.

## BE 통합 시나리오 실행

원본·후보·근거와 문서·연결을 request의 식별자로 준비하고, 입력 스냅샷 및 실행별 계약 버전을 고정한 뒤 가짜 AI 응답을 돌려준다. `events`의 `phase` 위치에서 상태 변경이나 저장 오류를 주입한다.

- `BEFORE_ACCEPTANCE`, `AFTER_ACCEPTANCE`, `BEFORE_COMPLETION`, `DURING_STORAGE`, `AFTER_COMPLETION`은 테스트 동기화 지점이다. 운영 이벤트 이름이 아니다.
- `DETACH_BASIS`는 `itemId`에 속한 `basisId = care_item_basis.id` 연결 행을 제거한다. `removeCareItemBasisRow: true`를 적용하고 행이 없어졌는지 확인한다. `basisKind`는 요청의 근거 종류와 일치해야 한다.
- `preserveBasisContent: true`는 SOURCE_RECORD이면 연결되었던 evidence·원본 소식·그 문서를, DIRECT_INPUT이면 연결되었던 원본 content_document를 유효한 상태로 보존하고 확인한다는 뜻이다. 연결 행 보존을 뜻하지 않는다. 모호했던 `keepBasisRow`·`keepDocument`는 제거했다. 응답이 참조하지 않은 입력 근거도 검증 대상이다.
- `proposalIndex`는 0부터 시작한다. 두 번째 저장 실패 뒤 부분 제안과 COMPLETED 상태가 남지 않아야 한다.
- `storedSuggestionsFile`은 내부 제안 필드 보존 기대값이며 공개 조회 DTO와 동일하다는 뜻이 아니다. 저장만으로 항목·답변을 확정하지 않는다.
- `completionState`·`storedAtCompletion`은 최초 완료 시점, `visibleValidProposalCount`·`visibleValidBasisCount`는 무효화 후 해당 분석 결과에서 정상 검토 대상으로 노출되는 제안·비교 근거의 개수다. 관리 항목 전체의 유효 근거 수가 0이라는 뜻은 아니다. 이후 상태 enum·삭제 API 형식은 새로 고정하지 않는다.
- `deletedSourceTextVisible`·`deletedSourceTextStoredInDerivedCopies`는 삭제된 원본에서 파생된 답변·변경분·비교 내용의 노출·저장 여부다. 다른 독립 근거를 일괄 삭제하라는 뜻이 아니다.
- `currentValues`, `mergedValues`는 공개 조회에서 대응하는 의미를 확인하는 테스트 표식이며 공개 응답 필드 이름을 강제하지 않는다.
- `INJECTED_BE_RESPONSE`의 `providerCalls: null`은 실제 AI 호출이 없고 BE 응답 검증만 수행함을 뜻한다. 알 수 없는 오류의 `SAFE_GENERIC_FAILURE`도 특정 미합의 코드 문자열을 강제하지 않는다.

### 공개 조회와 내부 상태

모든 BACKEND 사례의 `publicQuery`는 `GET /api/spaces/{spaceId}/source-records/{recordId}/analyses/{analysisId}`를 지정한다.
`spaceId`는 AI 요청의 `careSpaceId`, `recordId`는 분석 원본이다. `analysisIdFrom: CURRENT_RUN`은 해당 시나리오에서 접수한 BE 실행의 ID를 사용한다는 뜻이며 내부 `requestId`로 대체하지 않는다.
`phase: AFTER_SCENARIO`는 모든 이벤트와 비동기 결과 처리를 마친 뒤 조회함을 뜻한다. 완료 전 이벤트는 결과 저장 전에, 저장 후 이벤트는 최초 완료·저장 건수를 확인한 뒤 주입한다.

- `viewer: ACTIVE_SPACE_MEMBER`는 해당 공간에서 조회 권한이 있는 활성 구성원이다. `OTHER_ACTIVE_SPACE_MEMBER`는 참여가 종료된 요청자와 별개인 활성 구성원을 준비한다. 탈퇴한 요청자의 조회가 200이라는 뜻이 아니다.
- `expected.be.queryHttpStatus`·`queryErrorCode`는 이 공개 조회의 기대값이다. `state`·`storedProposalCount`는 내부 실행·저장 상태를 DB에서 별도로 확인한다. 404 응답에서 내부 DISCARDED 상태를 읽으려 하지 않는다.
- `C-source-deleted`: 완료 전 분석 원본 삭제 → 내부 DISCARDED·저장 0건, 공개 조회 404 RESOURCE_NOT_FOUND.
- `C-delete-derived-copies`: 최초 COMPLETED·저장 4건 확인 후 분석 원본 삭제 → 공개 조회 404 RESOURCE_NOT_FOUND, 파생 원본 내용 제거. 삭제 후 내부 상태 enum은 고정하지 않는다. 공개 404만으로 파생 내용 제거를 검증했다고 보지 않고 DB 및 관련 조회 경로도 확인한다.
- 원본은 활성 상태이고 후보 근거만 무효화한 사례는 공개 조회 200을 유지하며 정상 제안·근거로 노출되지 않는지 별도로 확인한다.
- `expected.ai.httpStatus`는 내부 AI 응답이다. BE가 늦은 결과를 폐기하거나 공개 조회가 404여도 이 값은 정상 응답 200일 수 있다.

### 직접 입력 근거의 독립적인 무효화

`setup.directInputDocuments`는 요청 전달 전에 준비할 DB 관계다. 각 항목의 `itemId`·`basisId`는 요청 후보의 DIRECT_INPUT 근거를 가리킨다.

1. `currentDocumentId`를 항목의 현재 문서로, 다른 ID인 `basisDocumentId`를 해당 care_item_basis.direct_document_id의 원본 문서로 연결한다. 둘 다 처음에는 유효한 문서여야 한다.
2. 현재 문서는 요청의 `currentValues`와 일치하도록 준비한다. 원본 직접 입력 문서는 해당 `basis.text` 원문을 보존한다. 현재 문서로 원본 근거를 대체하거나 DIRECT_INPUT.text를 재직렬화하지 않는다. 다른 후보·근거도 요청대로 준비한다.
3. `DETACH_BASIS`는 연결 행만 제거한다. `INVALIDATE_DIRECT_DOCUMENT`는 `documentId = basisDocumentId`의 본문을 null로 만들고 redacted_at을 설정한다(`redactBody: true`). 문서 행과 연결 행은 유지한다(`retainCareItemBasisRow: true`). FK가 남은 문서를 물리 삭제하는 시나리오가 아니다.
4. `expected.be.currentDocumentUnchanged`·`candidateVersionUnchanged`는 이벤트 전후 현재 문서의 ID·본문·유효성과 후보 버전이 그대로임을 검사한다. 분석 원본과 다른 입력도 유효하게 유지한다.
5. `basisAfterEvent`는 대상 연결 행의 존재, 원본 문서 행의 존재·유효성을 DB에서 확인하는 표식이다. 연결 해제 시 문서는 유효하게 남고, 문서 무효화 시 연결 행이 남는다.

직접 입력 사례는 완료 전/저장 후 × 연결 해제/문서 무효화 × 응답 참조/미참조의 8가지 조합이다.
미참조 사례는 응답이 대상으로 삼지도 참조하지도 않은 다른 입력 후보의 직접 입력 근거를 무효화한다. 응답의 basisReferences만 검증하거나 현재 문서·후보 버전만 검사하는 구현으로 통과해서는 안 된다.
완료 전에는 DISCARDED·저장 0건, 저장 후에는 최초 COMPLETED·저장 1건을 확인한 뒤 정상 제안·근거 노출 0건을 확인한다. 후자의 내부 최종 상태·무효 항목 표시 방식은 새로 고정하지 않는다.

기존 v1 데이터·호출·실패 동작 회귀 검증은 각 레포의 기존 테스트를 유지한다. BE에서 고정된 v1 실행을 새 v2 실행으로 변환하거나 자동 fallback하지 않는다.

## v2-20261016.2 보정 내역

- `C-source-deleted`, `C-delete-derived-copies`: 공개 조회를 404 RESOURCE_NOT_FOUND로 보정하고 내부 상태·파생 내용 제거 검증과 구분.
- 모든 BACKEND 사례: `publicQuery` 추가. `C-requester-inactive`는 다른 활성 구성원으로 조회.
- `C-basis-detached`, `C-unreferenced-basis`, `C-query-invalidated`: 연결 행 제거·원본 내용 보존으로 이벤트 의미 명확화.
- 추가 사례: `C-direct-basis-detached`, `C-direct-document-invalidated`, `C-direct-basis-detached-query`, `C-direct-document-invalidated-query`, `C-unreferenced-direct-basis`, `C-unreferenced-direct-document`, `C-unreferenced-direct-basis-query`, `C-unreferenced-direct-document-query`.
- 참조 사례용 `payloads/responses/update-direct-input.json` 추가. 요청 원문, 기존 응답 원문, 정적 계약·수신 기록 사례와 AI↔BE 오류 매핑은 유지.
- manifest의 `formatVersion: 1`은 파일 목록 형식을 유지한다는 뜻이다. 시나리오 이벤트·조회 메타데이터가 보완되었으므로 BE 소비 테스트는 revision과 함께 갱신해야 한다.

## 검증과 변경·전달 절차

AI 저장소 루트에서:

```bash
uv run --locked python scripts/fixture_manifest.py
uv run --locked python -m scripts.fixture_payloads
uv run --locked pytest tests/test_v2_fixture_bundle.py
uv run --locked pytest tests/test_v2_fixture_revision.py
uv run --locked pytest
```

변경은 먼저 JSON·기대값과 관련 계약을 검토한다. 기존 공유본과 구분할 revision을 정한 뒤 파일 해시 목록만 갱신한다.

```bash
uv run --locked python scripts/fixture_manifest.py --write --revision <새-revision>
uv run --locked python scripts/fixture_manifest.py
```

도구는 기대값을 수정하지 않는다. manifest만 맞춰 테스트를 통과시키는 용도로 사용하지 않는다. `manifest.json` 자체는 자기 해시를 넣지 않으며 원본 manifest 파일의 SHA-256을 전달 시 별도로 비교할 수 있다.

BE 전달에는 AI 커밋 SHA·fixture revision·추가/변경 caseId·검증 결과·아직 구현하지 않은 동작을 포함한다. BE 담당자가 전체 폴더를 복사하고 manifest의 파일 목록 및 각 SHA-256, caseId 목록을 비교한 뒤 Java 소비 테스트를 연결한다. 검토 중 차이는 원본 AI fixture에 함께 반영하고 새 revision으로 양쪽을 갱신한다. 한쪽만 기대값을 수정하지 않는다.

현재 데이터 준비·v1 회귀 검사와 후속 v2 구현 테스트·BE 테스트 결과를 별도로 보고한다. 양쪽 검토·실행 검증 전에 신규 분석을 v2로 전환하지 않는다.

## v2-20261021.1 변경·동기화 기준

- 추가/변경/삭제 caseId의 정확한 목록은 revision-history.json에 있다. 기존 A-* 146개·C-* 25개와
  payload 원문은 그대로이며 B-* 중 B-uncomparable-record의 외부 오류 기대값만 확정했다.
- 새 L-* 102개는 상한 18종의 -1/상한/+1, 자유 서술 필드별 경계, UTF-16·UTF-8·이스케이프·
  반복 값·정규화 전 집계를 포함한다. 새 H-* 25개는 실행 구현을 위한 수신·슬롯·경쟁·오류 시나리오다.
- `generated.json`의 payloads에는 116개 생성 바이트의 길이·SHA-256·UTF-16 측정값이 고정되어 있다.
  큰 본문을 여러 JSON 파일로 중복 저장하지 않는다. 생성 규칙과 Java 소비 방법은 [GENERATION.md](GENERATION.md).
- 독립 구성이 불가능한 초과 참조/evidence 사례는 coupledViolations를 읽는다. 다른 제한 초과를
  숨기거나 기대 오류를 구현에 맞춰 바꾸지 않는다. 이전 무제한 허용 런타임 테스트의 전환은 후속이다.
- 예전 전체 번들은 커밋 `760426fd6acc0cb0c0c3a81601e98696d80b5dde`의 이 경로에서 복원한다.
  원래 기준 출처 커밋은 `af3abb3310f2f86c55fdf182f687da7491ef5fec`이다.
  history/v2-20261016.2-manifest.json의 SHA-256은
  `a65c27c8a8c323f3bdfb6fec0bddefe8cf251870f940e2410a2043b0dab83ff9`로 고정한다.
  과거 manifest의 상대 경로는 **그 과거 커밋의 번들 루트** 기준이다. 현재 번들을 검증할 때는
  루트 manifest.json을 사용하며 history manifest를 현재 파일 목록으로 사용하지 않는다.
- BE 전달 기준 커밋은 이 변경을 실제 커밋·푸시한 뒤 전달한다. 미커밋 상태에서 기준 커밋을
  만들어 적지 않는다. 새 manifest의 SHA-256은 `sha256sum` 또는 PowerShell Get-FileHash로 계산한다.
- BE는 루트 manifest와 revision-history를 비교하고 파일 원문을 동기화한 뒤 생성 바이트 해시를
  검증한다. 이후 HTTP 오류 허용 목록·제한·통신 테스트를 연결하며 실행/저장 연결은 별도 진행한다.
