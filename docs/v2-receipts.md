# v2 수신 기록과 중복 호출 차단

이 문서는 AI 내부 구현과 사용 조건을 설명한다. 외부 계약은 [v2 확정 계약](ai-contract-v2.md)을
따르며, 공통 fixture 기준은 `v2-20261016.2`이다. 현재 FastAPI 실행 경로는 v1이고,
아래 v2 서비스는 HTTP 라우트·실제 LLM·BE 업무 DB에 연결하지 않았다.

## 저장소 선택과 적용 범위

Python 표준 라이브러리 `sqlite3`의 파일 저장소를 사용한다. 별도 DB 서버나 의존성을
추가하지 않고 현재 로컬 실행에서 영속 기록·트랜잭션·여러 프로세스 경쟁을 검증하기 위한 선택이다.
BE PostgreSQL 및 업무 테이블을 사용하지 않는다.

- **같은 호스트의 신뢰할 수 있는 로컬 파일시스템에서 동일한 DB 파일을 공유하는 프로세스**를 지원한다.
  프로세스마다 다른 파일이나 컨테이너별 임시 파일을 쓰면 서로 중복을 차단할 수 없다.
- 다중 호스트·네트워크 공유 드라이브·복제·고가용성 배포는 이번 어댑터의 지원 범위가 아니다.
  해당 배포가 필요하면 `ReceiptStore`의 원자성·영속성 계약을 충족하는 공유 저장소 어댑터가 필요하다.
- 초기 DB는 SQLite 기본 rollback journal을 사용하고 연결마다 `synchronous=FULL`을 적용한다.
  journal이 `OFF` 또는 `MEMORY`인 연결은 거부한다. 파일시스템·디스크의 내구성 및 백업 정책까지
  이 설정만으로 보장하지 않는다.
- `BEGIN IMMEDIATE` 안에서 기존 기록 확인·신규 삽입을 수행한다. `(contract_version, request_id)`
  복합 기본 키를 사용하며 커밋이 성공한 뒤에만 호출 권한을 반환한다.
- 최초 조회 이후 다른 프로세스가 먼저 등록할 수 있으므로 등록 트랜잭션에서 다시 확인한다.
  경쟁에서 패한 요청은 승자의 기록과 비교하며 기존 기록을 덮어쓰지 않는다.
- DB 잠금 대기 시간은 `lock_timeout_seconds`에 명시적으로 주입한다. 운영 기본값을 새로 정하지
  않았다. 테스트의 0초·2초는 장애 주입·경쟁 검증용 값이다. 잠금 대기 한도 초과는 저장소 장애다.

근거: [Python sqlite3의 연결·트랜잭션 처리](https://docs.python.org/3.13/library/sqlite3.html),
[SQLite IMMEDIATE 트랜잭션](https://www.sqlite.org/lang_transaction.html),
[SQLite synchronous 설정](https://www.sqlite.org/pragma.html#pragma_synchronous).

## 저장하는 데이터

| 필드 | 의미 |
| --- | --- |
| `contract_version` | 내부 계약 버전, 현재 서비스는 `v2` 고정 |
| `request_id` | 소문자·하이픈 형태의 UUID |
| `canonicalization_version` | 동일 본문 정규화 규칙 버전 |
| `hmac_key_version` | 비교에 사용한 키 버전, 비밀키 자체가 아님 |
| `digest` | HMAC-SHA-256 결과 32바이트 |

원문·정규화한 본문·제안·응답·인증키·HMAC 비밀키는 기록에 저장하지 않는다.
결과 캐시·실행 상태·TTL·자동 삭제 작업도 없다. 성공·실패·진행 중 여부에 관계없이 기록이 있으면
차단하므로 실행 상태 컬럼이 필요하지 않다. fixture의 `executionState`는 준비 조건이며 DB enum이 아니다.
스키마 버전은 SQLite `user_version=1`로 구분한다. 알 수 없는 스키마를 자동 변환·초기화하지 않는다.
버전·컬럼 외에 해당 버전의 생성 DDL 전체를 비교하여 digest의 BLOB 타입·32바이트 CHECK,
NOT NULL, 복합 기본 키, WITHOUT ROWID를 확인한다. 앱이 생성한 DDL을 기준으로 하므로 수동으로
다르게 작성한 스키마는 의미가 같더라도 지원하지 않는다. 등록 입력의 UUID·버전 메타데이터·지문
형식도 DB 접근 전에 검증한다. 데이터는 바인딩하며 `user_version` 설정은 고정 SQL 리터럴이다.

보존 중인 기록만 중복 차단의 근거다. 보관 기간·정리 정책은 미정이며, 자동 삭제가 없다는 사실은
영구 보존 정책이나 파일 손실 이후까지의 중복 차단을 보장하지 않는다.

## 정규화와 HMAC

`comparison.py`의 `v2-json-1` 규칙은 검증된 원본 스냅샷에서 별도 표현을 생성한다.
객체 키를 재귀 정렬하고 선언된 UUID만 정규화하며, `recordedAt`은 UTC·소수초 6자리·Z로 표현한다.
배열 순서·문자열 공백·줄바꿈·유니코드 문자는 보존한다. `DIRECT_INPUT.text`는 내부 JSON으로
해석하지 않는 문자열이며, 문자열 안 UUID처럼 보이는 내용도 바꾸지 않는다.

UTC 변환은 날짜 순번과 정수 마이크로초로 계산한다. 유효한 0001년·9999년 입력이 오프셋 때문에
Python datetime 범위를 벗어나도 처리하며, 비교 표현에만 `0000-12-31`·`10000-01-01`을 사용한다.
원본 `recordedAt`과 제공자 입력은 유지한다. 기존에 정상 정규화되던 입력의 바이트는 동일하고,
과거에는 등록 전에 실패하던 경계 입력을 보완하므로 규칙 버전 `v2-json-1`은 유지한다.

직렬화는 공백 없는 JSON(`separators=(",", ":")`, `sort_keys=True`, `ensure_ascii=True`)의 UTF-8이다.
ASCII 이스케이프는 비교 표현이며 원본 스냅샷·제공자 입력·응답의 텍스트를 변경하지 않는다.
HMAC 입력은 `["hanmaum-ai-receipt", "v2", 규칙 버전, 키 버전]`의 공백 없는 JSON,
NUL 바이트 하나, 정규화 본문의 순서다. 비교는 `hmac.compare_digest`를 사용한다.

키는 `SecretBytes`로 별도 주입하며 32바이트 이상을 요구한다. 길이 검사로 무작위성까지 확인할 수는
없으므로 실제 키는 안전한 난수로 생성하여 별도 비밀 저장소에서 지속 관리해야 한다.
내부 인증키·모델 API 키·BE 키를 자동으로 읽거나 재사용하지 않으며, 누락 시 새 키를 자동 생성하지 않는다.

키 버전에는 한 키만, 규칙 버전에는 한 직렬화 의미만 연결한다. 같은 버전 이름으로 내용을 바꾸지 않는다.
활성 버전 변경 시에는 새 요청만 새 버전을 사용하고, 기존 기록은 그 기록에 저장된 이전 버전의 키와
규칙으로 비교한다. `RequestDigester`는 주입된 매핑을 복사하여 사용한다. 이전 키·규칙 제거 전에는
잔존 기록·보관 정책을 검토해야 한다. 운영 교체 주기·배포·폐기 절차는 후속 결정이다.

이전 버전을 사용할 수 없거나 안전한 비교가 불가능하면 `ComparisonUnavailable`로 차단한다.
이 예외에는 HTTP 상태·오류 코드가 없다. BE 합의 전 임의로 409·503 중 하나에 매핑하지 않는다.

## 사용 경계와 초기화

`SQLiteReceiptStore(path, lock_timeout_seconds=...)`는 절대 파일 경로를 받는다.
상위 디렉터리는 준비되어 있어야 하며 파일·디렉터리 권한은 실행 계정으로 제한한다.
`initialize()`는 새 저장소를 준비하는 명시적 관리 작업이다. 기존 정상 스키마에서는 기록을 보존한다.
**요청마다 또는 서버 시작마다 유실 복구 용도로 호출하면 안 된다.**

일반 `get`·`register`는 `mode=rw`로 이미 존재하는 파일만 열며, 누락·빈 파일·잘못된 스키마·손상은
`RECEIPT_STORE_UNAVAILABLE`로 처리한다. 파일 손실 시 빈 DB로 재시작하면 과거 중복 차단 근거를 잃는다.
자동 재생성이나 장애 시 메모리 저장소 fallback은 제공하지 않는다.

후속 조립 코드에서의 사용 형태:

```python
from pathlib import Path
from pydantic import SecretBytes

from app.v2.comparison import RequestDigester
from app.v2.receipts import ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService

# 아래 값과 provider는 호출부가 제공한다. 기존 v1 Settings나 .env는 읽지 않는다.
store = SQLiteReceiptStore(Path(receipt_db_path), lock_timeout_seconds=receipt_lock_wait)
# 새 저장소 준비 작업에서만 store.initialize() 실행
comparison = RequestDigester(
    keys={key_version: SecretBytes(dedicated_hmac_key)},
    active_key_version=key_version,
)
service = AnalysisService(receipts=ReceiptGate(store, comparison), provider=provider)
# 인증·헤더 UUID 확인·입력 크기 제한을 마친 호출부에서만 실행
result = await service.analyze(raw_request_bytes)
```

환경 변수 이름·배포 기본 경로·운영 잠금 대기 시간을 아직 추가하지 않았다.
위 생성자 주입으로 테스트·내부 조립을 지원하며 기존 v1 시작에 DB나 HMAC 키를 요구하지 않는다.
로컬 `*.sqlite3` 및 sidecar 파일은 Git에서 제외한다. 저장 경로는 소스 디렉터리 밖에 두는 것을 권장한다.

## 처리 순서와 장애 의미

1. `parse_request(raw)`로 원문 JSON을 검증하고 불변 스냅샷 생성.
2. 제공자 미설정이면 `AI_NOT_CONFIGURED`로 차단.
3. 워커 스레드에서 기존 기록 비교 및 원자적 등록. 각 작업에서 독립 연결 사용.
4. 신규 등록의 커밋 성공 후 별도 asyncio Task에서 제공자 호출, 호출자는 독립된 기한으로 대기.
5. 최초 스냅샷으로 `validate_response` 실행. 무효 제안 하나라도 있으면 전체 거부.

| 조건 | 내부 결과 | 이번 요청의 제공자 호출 |
| --- | --- | --- |
| 같은 ID·같은 본문 | `409 REQUEST_ALREADY_RECEIVED` | 0 |
| 같은 ID·다른 본문 | `409 REQUEST_ID_REUSED` | 0 |
| 저장소 조회·등록·커밋 실패 또는 결과 불확실 | `503 RECEIPT_STORE_UNAVAILABLE` | 0 |
| 기존 기록의 안전한 비교 불가 | `ComparisonUnavailable`, HTTP 매핑 미정 | 0 |
| 제공자 예외·무효 결과·타임아웃 | 기존 합의의 502·504 오류 | 최초 호출만 허용 |

제공자 타임아웃은 기존 기본 20초·최대 25초를 유지하며, DB 잠금 대기 시간과 별도다.
후속 HTTP 연결 시 전체 응답 예산 안에서 저장소 대기·모델 대기·검증 시간을 함께 조정해야 한다.
제공자 원본 예외와 임의의 `AnalysisFailure` 메시지는 외부로 전달하지 않는다.
제공자가 입력을 가공한다면 `request.model_dump(mode="json")` 사본을 사용한다.

대기 기한은 단조 시계로 확인하고 제공자가 취소를 억제하더라도 늦은 결과를 성공으로 반환하지 않는다.
시간 초과 시 `AI_TIMEOUT`을 반환하고 제공자 Task에 취소를 요청한다. 호출자 취소도 제공자 정리를
기다리지 않는다. 미완료 Task는 강한 참조로 유지하며 완료 시 참조를 해제하고 예외를 회수한다.
늦게 발생한 제공자 오류도 원본 예외가 asyncio 미처리 오류 로그에 출력되지 않도록 처리한다.
이는 제공자 실행·과금의 강제 중단을 보장하지 않는다. 어댑터는 이벤트 루프를 막지 않아야 하며
취소를 전파해야 한다. 취소를 무시한 작업의 자원은 완료까지 남고, 동시 처리 예산은 후속 연결 시 결정한다.

등록 후 실패·무효 응답·통신 취소·결과 유실이어도 기록을 제거하지 않는다. 등록 워커가 취소된
코루틴보다 늦게 커밋할 수 있지만 워커는 제공자를 호출하지 않는다. 이 경우 후속 동일 요청은 차단된다.
커밋 실패로 보고된 작업이 실제로 기록을 남겼을 수 있어도 자동 재시도하지 않는다.
수신 기록은 모델 호출의 실행 권한이며, 성공 분석 결과나 과금 취소를 보장하지 않는다.

## 민감 정보를 제외한 장애 진단

`app.v2.diagnostics`는 고정된 `failure_event`·`failure_kind`만 warning 로그와 실패 횟수에 기록한다.
제공자 실패·타임아웃·늦은 실패·무효 결과, 저장소 초기화·조회·등록·스키마 오류,
비교 규칙/키 누락·규칙 실행 실패·메타데이터 오류를 구분한다. SQLite는 허용 목록의 숫자 오류
코드를 잠금·제약·입출력 등 고정 분류로 바꾸며 외부 오류명은 사용하지 않는다.

요청 ID·원문·응답·키/규칙 버전 값·비밀키·지문·DB 경로·SQL·예외 메시지·사용자 정의 예외 클래스명·
traceback은 기록하지 않는다. 외부 오류 응답의 코드·메시지 계약은 유지한다.
`failure_counts()`는 `(event, kind)`별 누적 횟수의 사본을 반환하며 워커 스레드에서도 안전하게 집계한다.
현재는 프로세스별 메모리 카운터로 재시작 시 초기화된다. 외부 메트릭 수집·프로세스 간 합산·경보
연결은 배포 관측 체계에 맡기며 이번 작업에 HTTP 메트릭 엔드포인트를 추가하지 않는다.

## 검증 범위

```bash
uv run --locked pytest tests/test_v2_receipts.py
uv run --locked pytest tests/test_v2_receipt_review.py
uv run --locked pytest
uv run --locked python scripts/fixture_manifest.py
```

- B-* 30개 전체 시나리오를 실제 SQLite·내부 서비스·가짜 제공자에 연결한다.
  요청은 fixture 원문 바이트로 전달하며 같은 본문 기대값도 검증한다.
- `B-concurrent`는 별도 서비스·저장소 인스턴스 두 개를 초기 조회 이후 barrier로 동기화하고,
  결과 다중집합·전체 호출 1회·기록 1개를 확인한다.
- 별도 spawn 프로세스의 동일/상이 본문 경쟁과 새 프로세스 재시작도 검증한다.
- 저장소 읽기·삽입·잠금·커밋 오류, 파일 유실·스키마 손상, 등록/호출 중 취소,
  제공자 실패·타임아웃·무효 결과 이후 재수신 차단 및 원문·비밀 비저장을 검증한다.
- UTC 범위 밖 경계 시각·기존 정규화 바이트 호환, 취소를 억제하는 제공자의 기한·늦은 오류 정리,
  CHECK 누락·약화·주석 위장, 잘못된 지문의 DB 접근 전 차단, 바인딩된 SQL 입력,
  장애 분류·실패 집계·진단 로그의 민감 정보 비노출을 회귀 검증한다.
- fixture의 `httpStatus`·`code`는 내부 예외 속성으로 비교하며 **실제 HTTP 응답 검증이 아니다.**
  `expected.be`의 상태·제안 저장·조회는 이번 테스트에서 실행하지 않는다.
- 이전 v1 기록 시드는 계약 버전 키 분리만 검증한다. v1에 수신 기록 기능을 추가하지 않는다.
- 실제 모델·유료 호출·품질 평가와 BE DB 통합은 수행하지 않는다.

## 후속 합의 및 연결

다음 v2 HTTP 연결 이슈를 정할 때 비교 불가 오류의 상태·코드 및 BE 매핑, 요청·응답 바이트와
컬렉션·문자열 제한, 동시 처리 예산·초과 오류를 합의한다. 외부 입력은 전체 버퍼링·디코딩 전에
읽기 단계부터 보호해야 한다. 현재 내부 파서·서비스를 크기 제한 없는 HTTP 경로에 바로 노출하지 않는다.

보관 기간·정리 후 중복 차단 범위는 자동 삭제·만료 적용 전, 늦어도 운영 전에 합의한다.
이 작업의 완료는 내부 구현·저장소 실행 검증 완료이며, BE↔AI HTTP 연동 준비나 운영 전환 완료가 아니다.
