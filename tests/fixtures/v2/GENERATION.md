# v2-20261021.1 경계 데이터 재현

generated.json은 언어와 무관한 JSON 데이터다. Python 참조 구현은 AI 저장소의
scripts/fixture_payloads.py이며 표준 라이브러리만 사용한다. BE는 아래 규칙으로 Java에서
재현하거나 AI에서 materialize한 JSON 원문을 전달받아 읽을 수 있다. 해시/길이가 같은지 확인한다.
기대 HTTP·오류는 cases/limits.json과 cases/http.json에 별도로 고정되어 있다.

## 레시피 실행 규칙

1. baseFile은 번들 루트 상대 경로의 JSON 원문을 디코딩하고 깊은 사본으로 시작한다.
   baseRecipe는 해당 레시피의 생성 바이트를 디코딩한 사본이다. 순환 참조는 거부한다.
2. operations를 배열 순서대로 적용한다. path/paths는 JSON Pointer이며 배열 첨자는 0부터,
   `~1`은 `/`, `~0`은 `~`로 해석한다. 객체의 마지막 키는 새로 추가할 수 있다.
3. `set`: 지정 위치의 값을 value로 교체한다.
4. `repeatText`: text를 count회 이어 붙인 문자열로 교체한다. count는 UTF-16 길이가 아닌 반복 수다.
5. `appendText`: 기존 문자열 뒤에 text를 count회 붙인다.
6. `fillStringTotal`: 현재 디코딩 JSON의 모든 문자열 값 합계와 units의 차이를 계산한다.
   paths 순서대로 각 문자열의 fieldMaxUnits 잔여량까지 ASCII `a`를 붙인다. 숫자·boolean·null·키는
   제외하고 반복 값은 매번 센다. 현재 합계가 더 크거나 용량이 부족하면 생성 실패다.
7. 객체 키를 재귀적으로 사전순 정렬하고 `,`·`:` 구분자 주변 공백 없이 JSON으로 직렬화한다.
   이 번들의 키는 모두 ASCII다. 배열 순서는 유지한다. 숫자는 정수 십진 표현이다.
8. encoding.ensureAscii 기본값은 true다. 비ASCII는 소문자 4자리 `\uXXXX`로 이스케이프하고
   보충 문자는 UTF-16 서로게이트 쌍으로 각각 이스케이프한다. false면 UTF-8로 직접 인코딩한다.
   `/`는 이스케이프하지 않는다. `"`·`\` 및 제어문자는 표준 JSON escaping을 적용한다
   (`\b`, `\f`, `\n`, `\r`, `\t`, 나머지 U+0000~001F는 `\u00xx`).
9. BOM과 끝 개행은 넣지 않는다. encoding.padToBytes가 있으면 직렬화 뒤 ASCII 공백(0x20)을
   덧붙여 정확히 해당 길이로 만든다. 더 작게 지정하면 실패한다. 이 공백은 문자열 합계에 포함하지 않는다.

payloads의 모든 항목은 side(request/response)와 expected를 갖는다. byteLength는 패딩까지 포함한
전체 바이트, sha256은 그 바이트의 소문자 16진 SHA-256, stringUnits는 타입 변환 전 전체 문자열
합계다. measurements는 limits.json과 같은 키를 쓰며 집합형 필드의 경계는 최대/합계로 표시한다.
request.currentText/response.changeText는 합의한 자유 서술 필드만 검사한다.

## 검증·내보내기

AI 저장소 루트에서 실행한다. 검사 모드는 어떤 파일도 고치지 않는다.

```bash
uv run --locked python -m scripts.fixture_payloads
uv run --locked python -m scripts.fixture_payloads --export test-results/v2-20261021.1-payloads
```

export는 존재하지 않는 새 디렉터리만 허용하며 공통 fixture 번들 내부에는 만들지 않는다.
생성 파일명은 `<recipe-name>.json`이다. BE에서 ObjectMapper로 다시 직렬화해 전송하면 바이트
경계가 바뀔 수 있으므로 생성 바이트 그대로 사용한다. 생성물은 Git에 중복 저장할 필요가 없다.

작성자가 의도한 레시피를 수정한 경우에만 아래 명령으로 길이·해시를 다시 산출하고 diff를 검토한다.
이 명령은 HTTP 상태·오류·위반 기대값을 추론하거나 수정하지 않는다.

```bash
uv run --locked python -m scripts.fixture_payloads --write-metadata
```

## 테스트 구분과 시나리오 해석

- LIMITS의 boundary.metric/value는 측정 대상이다. expectedLimitViolations에는 초과한 제한만
  기록한다. coupledViolations는 참조 중복·원문에 없는 evidence 등 함께 위반되는 기존 규칙이다.
  HTTP 기대값은 동일하게 전체 실패이며 특정 오류 메시지로 거부 순서를 추측하지 않는다.
- measurementOnly 사례는 UTF-16·이스케이프·UUID 정규화 전 길이 등의 계산 증거다.
  완성된 HTTP 분석 시나리오나 성공 응답을 가정하지 않는다.
- HTTP의 setup은 격리된 저장소와 슬롯 상태를 준비하는 조건이고 steps는 동기화 지점이다.
  requestBodyFile/storedRequestBodyFile/competitorBodyFile은 각각 현재·기존·경쟁 요청 원문이다.
  otherRequestBodyFile은 슬롯 점유 중 별도로 제출하는 신규 요청이다. bodyRecipe는 generated.json
  이름이며 신규 요청의 ID는 기존/진행 중 요청과 충돌하지 않게 준비한다.
- `existingReceipt: uncomparable`은 실제 기록은 존재하지만 해당 키/규칙을 사용할 수 없는 상태다.
  가짜로 HTTP만 반환해 AI 중복 우선순위 실행 검증을 대체하지 않는다.
- `PAUSE_AFTER_ABSENT_READ` 이후 경쟁 요청의 커밋을 제어해 원자적 재확인을 검증한다.
  `REJECT_THIS_REQUEST` 뒤의 경쟁 등록은 판정 이후에 일어나므로 과부하 요청이 기록을 만들었다고
  판정하지 않는다. 전역 기록 수 0을 기대하지 않는다.
- 슬롯 유지 시나리오는 두 슬롯 중 한 제공자를 취소 억제 상태로 만들고 timeout/호출자 취소 후에도
  2개 점유임을 확인한다. 다른 신규 요청은 503, 제공자 실제 종료 후에는 1개 점유로 내려가야 한다.
  호출자 취소는 HTTP 응답을 받지 못하므로 status/code는 null이고 기록 보존을 별도 검사한다.
- Chunked 시나리오는 Content-Length를 생략하고 지정 chunkBytes 크기로 쪼갠 생성 원문을 전달한다.
  AI 수신과 BE 수신을 구분한다. 버퍼에 상한 초과 데이터를 계속 쌓거나 전체 읽기 후 검사하지 않는다.
- 오류 응답 경계도 유효한 code/message JSON 뒤의 공백으로 길이를 맞추고 해시를 고정한다.
  정상 크기의 503 AI_OVERLOADED는 AI_SERVICE_UNAVAILABLE로, 바이트 초과 시 AI_INVALID_RESPONSE로
  처리한다. 오류 본문에는 suggestions가 없으므로 해당 컬렉션/필드의 측정값은 0이다.
- BE 주입 시나리오는 실제 AI 호출을 뜻하지 않는다. 특히 미등록 HTTP/code 조합 및 과대 200/오류
  응답은 BE가 독립 거부해야 한다. 저장/조회 단계를 연결하기 전에는 DB 완료로 보고하지 않는다.
- `executionCoverage`의 DEFERRED는 의도적인 구현 경계다. 명세 검사 통과를 HTTP·슬롯·BE DB
  통합 통과로 보고하지 않으며 후속 구현 이슈에서 모든 새 caseId를 실행/미실행 목록으로 관리한다.
