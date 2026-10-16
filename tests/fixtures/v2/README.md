# BE↔AI v2 공통 fixture

계약 기준: 2026-10-16 확정본. 전체 계약은 AI 저장소의 `docs/ai-contract-v2.md`.
현재 공유 revision은 `manifest.json`의 `revision`이다. AI에서 만든 최초 데이터이며 BE 복사·검토·실행 검증은 별도 진행한다.

이 디렉터리 전체를 BE의 `src/test/resources/ai-contract/v2/`에 복사한다.
`cases/`, `payloads/`, `error-mapping.json`, `manifest.json`, 이 문서와 `.gitattributes`까지 동일하게 관리한다.
Python 테스트와 manifest 도구는 AI 저장소에만 두며 BE는 같은 사례를 Java 테스트에서 읽는다.

## 무엇이 검증되는가

| 모음 | 소비할 테스트 | 현재 AI에서 실행하는 범위 |
| --- | --- | --- |
| `cases/static.json` | v2 HTTP 요청·제공자 결과 검증, BE 오류 매핑 | 파일 무결성·참조·기대값 일관성 |
| `cases/receipts.json` | 실제 수신 기록 어댑터의 순차·동시·재시작·키 교체 검증 | 시나리오 형식·호출 수·비교 사례 일관성 |
| `cases/backend.json` | BE의 DB 트랜잭션·권한·입력 의존성·조회 통합 테스트 | 상태 변경 시나리오와 데이터 참조 일관성 |

현재 v2 endpoint, 실제 모델, 수신 기록 저장소는 구현하지 않았다. Python의 fixture 검사 통과는 위 기능이나 BE 통합 테스트 통과를 뜻하지 않는다. 기존 v1 테스트는 그대로 별도 실행한다. 실제 의미 정확도와 모델 품질도 별도 평가한다.

## 공통 데이터 형식

각 모음은 사례 배열이다. `caseId`는 전체 모음에서 유일하며 목적이 바뀌면 새 ID를 사용한다.

- `category`: `STATIC`, `RECEIPT`, `BACKEND`.
- `purpose`: 검증 목적. `contractSections`는 최종 계약의 관련 절 번호.
- `contractVersion`: 실행 계약 메타데이터. 요청 JSON에 새 필드를 추가하는 것이 아니다.
- `request`: `method`, `path`, `headers`, `bodyFile`. 파일 경로는 항상 이 디렉터리 기준이다.
- `provider`: `RETURN`이면 `bodyFile`의 JSON을 반환하는 가짜 제공자. `UNAVAILABLE`은 호출 중 예외, `TIMEOUT`은 완료하지 않는 호출, `DISABLED`는 제공자 미설정이다.
- `expected.ai`: HTTP 상태, 성공 `bodyFile` 또는 오류 `code`, `providerCalls`. `messagePolicy: NON_EMPTY_SANITIZED`는 비어 있지 않은 안전한 문자열만 요구하며 문구를 새로 고정하지 않는다.
- `expected.be`: 실행 상태·실패 코드·저장 건수 등 해당 사례에서 판정할 항목만 명시한다. 명시하지 않은 필드나 오류 메시지는 새 계약이 아니다.
- 성공의 `bodyFile` 비교는 JSON 객체 기준이며 UUID는 값으로 비교한다. 원문 전송 바이트 자체를 성공 응답과 비교하는 것은 아니다.
- 입력 인증키 `fixture-only-not-a-secret`는 합성 테스트용 상수다. 환경 설정에도 같은 테스트 상수를 사용하고 운영 비밀값을 읽지 않는다.

파일을 `bodyFile`에서 읽은 UTF-8 **바이트 그대로** 전송한다. `json=...`, ObjectMapper read/write 등으로 요청을 재직렬화하면 중복 키·이스케이프·소수초 표현 사례가 사라질 수 있다. Python HTTPX는 `content=path.read_bytes()`, Java 클라이언트는 원문 byte[] 전송을 사용한다.

manifest의 SHA-256은 **파일 원문 바이트의 동기화 검사**다. 런타임의 동일 요청 판정용 HMAC이 아니며, 해시가 다른 파일도 정규화 후 동일 요청일 수 있다. `.gitattributes`는 checkout 시 LF를 유지한다. 파일 자동 정렬·재포맷·줄바꿈 변환을 피한다.

## 정적 사례 실행

1. 각 사례마다 격리된 수신 기록 저장소와 설정을 준비한다. 사례 사이의 같은 requestId가 다른 사례를 차단하지 않게 한다.
2. `provider`의 동작과 호출 횟수 계측을 설치하고 `request`를 전송한다.
3. 실제 HTTP·본문·오류·제공자 호출 횟수를 `expected.ai`와 비교한다.
4. BE 테스트에서는 같은 응답을 내부 HTTP 어댑터에 주입하고 `expected.be`를 확인한다. 상태 조회의 HTTP 200은 내부 AI 오류 상태와 별개다.

무효 요청도 정상 가짜 제공자를 설정한다. 호출 0회가 실제로 보장되는지 확인하기 위한 것으로, 가짜 제공자를 생략해 검증을 통과시키지 않는다. 무효 제공자 결과는 호출 1회·전체 결과 거부·BE 저장 0건이 기대값이다.

## 수신 기록 시나리오 실행

- `steps`는 배열 순서대로 실행한다. `SUBMIT`의 호출 수는 해당 요청의 추가 호출 수다. 최상위 `totalProviderCalls`는 시나리오 전체 수다.
- `seedReceipt`는 이전에 등록된 기록의 테스트 준비 조건이다. `executionState`의 SUCCEEDED·FAILED·IN_PROGRESS는 시나리오 용어이며 저장소 enum이나 스키마를 새로 정하지 않는다. 이 시드에 원문이 있는 것은 테스트 준비용이며 실제 수신 기록에 원문을 저장하라는 뜻이 아니다.
- `FAIL_RECEIPT_STORE`는 READ 또는 REGISTER 단계 실패를 주입한다. `RESTART_AI_PRESERVE_RECEIPTS`는 앱 인스턴스를 새로 만들되 저장소를 유지한다.
- `SUBMIT_CONCURRENT`는 같은 저장소를 공유하는 서로 다른 두 AI 인스턴스에 동시에 제출한다. 결과 순서는 무관하며 `aiResults`의 다중집합과 전체 호출 수 1을 확인한다. 순차 호출로 대체하지 않는다.
- `ROTATE_COMPARISON_METADATA`의 버전 문자열은 합성 표식이다. 실제 키·보관 기간·회전 정책을 정의하지 않는다. 이전 기록을 비교 가능한 경우 동일 요청은 차단한다.
- 안전하게 비교할 수 없는 기록의 `outcome: BLOCKED`는 호출 0회·결과 저장 없음만 고정한다. 최종 계약에 구체 오류 매핑이 명시되지 않은 이 조건에 임의 HTTP·코드를 추가하지 않았다.
- `bodyEquality`는 정규화 후 동일 본문 판정의 기대값이다. SHA-256의 파일 동일성과 혼동하지 않는다.
- 개별 SUBMIT의 BE 기대값은 그 응답을 BE에 독립 주입했을 때의 매핑이다. 연속 단계가 같은 BE 실행의 상태를 덮어쓴다는 뜻이 아니다. 동시 수신 사례도 실제 실행 완료 결과를 중복 오류로 덮어쓰지 않는다.

## BE 통합 시나리오 실행

원본·후보·근거와 문서·연결을 request의 식별자로 준비하고, 입력 스냅샷 및 실행별 계약 버전을 고정한 뒤 가짜 AI 응답을 돌려준다. `events`의 `phase` 위치에서 상태 변경이나 저장 오류를 주입한다.

- `BEFORE_ACCEPTANCE`, `AFTER_ACCEPTANCE`, `BEFORE_COMPLETION`, `DURING_STORAGE`, `AFTER_COMPLETION`은 테스트 동기화 지점이다. 운영 이벤트 이름이 아니다.
- `DETACH_BASIS`의 `keepBasisRow`·`keepDocument`가 true이면 연결만 해제한다. 응답이 참조하지 않은 입력 근거도 검증 대상이다.
- `proposalIndex`는 0부터 시작한다. 두 번째 저장 실패 뒤 부분 제안과 COMPLETED 상태가 남지 않아야 한다.
- `storedSuggestionsFile`은 내부 제안 필드 보존 기대값이며 공개 조회 DTO와 동일하다는 뜻이 아니다. 저장만으로 항목·답변을 확정하지 않는다.
- `completionState`·`storedAtCompletion`은 최초 완료 시점, `visibleValidProposalCount`·`visibleValidBasisCount`는 무효화 후 정상 항목으로 노출되는 개수다. 이후 상태 enum·삭제 API 형식은 새로 고정하지 않는다.
- `deletedSourceTextVisible`·`deletedSourceTextStoredInDerivedCopies`는 삭제된 원본에서 파생된 답변·변경분·비교 내용의 노출·저장 여부다. 다른 독립 근거를 일괄 삭제하라는 뜻이 아니다.
- `currentValues`, `mergedValues`는 공개 조회에서 대응하는 의미를 확인하는 테스트 표식이며 공개 응답 필드 이름을 강제하지 않는다.
- `INJECTED_BE_RESPONSE`의 `providerCalls: null`은 실제 AI 호출이 없고 BE 응답 검증만 수행함을 뜻한다. 알 수 없는 오류의 `SAFE_GENERIC_FAILURE`도 특정 미합의 코드 문자열을 강제하지 않는다.

기존 v1 데이터·호출·실패 동작 회귀 검증은 각 레포의 기존 테스트를 유지한다. BE에서 고정된 v1 실행을 새 v2 실행으로 변환하거나 자동 fallback하지 않는다.

## 검증과 변경·전달 절차

AI 저장소 루트에서:

```bash
uv run --locked python scripts/fixture_manifest.py
uv run --locked pytest tests/test_v2_fixture_bundle.py
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
