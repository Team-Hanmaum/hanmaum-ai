# v2 로컬 실행과 BE 연동 확인

공통 계약·fixture 기준은 `v2-20261021.1`이다. AI 내부 평가 데이터의 revision은 별도로 관리하며, 평가 사례나 프롬프트 변경만으로 BE 공통 fixture를 교체하지 않는다. 전체 필드·제한·오류 기준은 [v2 계약](ai-contract-v2.md)을 따른다.

이 문서는 재현 절차다. AI 단독 HTTP 확인과 실제 모델 평가는 BE의 원본 저장 → AI 호출 → 제안 저장·조회까지의 통합 검증을 대신하지 않는다. **실제 BE와 AI를 연결한 전체 경로는 별도로 검증해야 한다.**

## 1. 로컬 설정과 서버 실행

AI 레포 루트에서 `uv sync --locked`를 실행한다. 기존 `.env`의 stub 설정과 키·수신 저장소를 보존하고, 실제 모델용 실행에서는 Git에서 제외한 `.env.openai.local`을 추가로 읽는다.

| 설정 위치 | 설정 | 용도 |
| --- | --- | --- |
| `.env` | `APP_ENV=local`, `AI_V2_PROVIDER=stub` | 기존 8000 서버의 로컬 동작 |
| `.env` | `OPENAI_API_KEY` | 실제 OpenAI 호출 인증. BE나 Swagger에 전달하지 않음 |
| `.env` | `AI_V2_RECEIPT_DB_PATH` | 초기화된 AI 전용 SQLite 파일의 절대 경로 |
| `.env` | `AI_V2_HMAC_KEYS`, `AI_V2_ACTIVE_HMAC_KEY_VERSION` | 기존 수신 기록 비교에 사용하는 별도 키와 버전 |
| `.env.openai.local` | `AI_V2_PROVIDER=openai` | 8001 서버의 실제 모델 활성화 |
| `.env.openai.local` | `AI_INTERNAL_API_KEY` | 8001 서버에만 적용할 별도 무작위 내부 인증키 |

`.env.openai.local`에는 마지막 두 설정만 둔다. `OPENAI_API_KEY`·HMAC 키를 복제하지 않으며, 내부 인증키는 이 두 키와 다른 값을 사용한다. 공개 예시 키 `local-development-only`는 실제 OpenAI 모드에서 허용하지 않는다. 이 파일을 Git·채팅·실행 로그에 올리지 않는다.

기존 수신 저장소 준비 방법은 [README](../README.md)의 v2 HTTP 실행 안내를 따른다. AI 단독 실행에는 BE PostgreSQL이나 Docker가 필요하지 않다. 기존 SQLite 파일과 HMAC 키를 삭제·교체해서 중복 차단을 우회하지 않는다. 두 서버가 같은 수신 저장소를 사용하면 stub에서 사용한 ID도 실제 모델 서버에서 중복으로 판정된다.

서로 다른 PowerShell 터미널에서 실행한다. 이미 해당 포트에 서버가 있으면 중복 기동하지 않는다.

```powershell
# 기존 .env가 stub인 8000 서버
uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

```powershell
# 실제 모델용 8001 서버
uv run --locked uvicorn app.main:create_app --factory --env-file .env.openai.local --reload --host 127.0.0.1 --port 8001
```

`--env-file`의 값은 해당 실행 프로세스에 적용되고, 나머지 설정은 앱이 기존 `.env`에서 읽는다. **이미 설정된 프로세스 환경 변수가 파일보다 우선**하므로, 이전에 `$env:AI_V2_PROVIDER`나 `$env:AI_INTERNAL_API_KEY`를 지정했다면 의도한 값과 일치시키거나 해당 임시 override를 해제해야 한다. 비밀값을 출력해서 확인하지 않는다.

8001 실행에도 기존 `.env`의 `AI_V2_MODEL`, `AI_V2_REASONING_EFFORT`, `AI_V2_SERVICE_TIER`가 적용된다. 초기 기준은 `gpt-6-luna`·`low`·`default`다. 각 터미널의 `Ctrl+C`로 해당 서버를 종료한다. 8001의 내부 인증키는 그 프로세스의 v1·v2 API 모두에 적용되며, 별도 실행 중인 8000의 설정은 바꾸지 않는다.

## 2. Swagger에서 성공과 중복 확인

1. `http://127.0.0.1:8001/docs`를 열고 **Authorize**에 `.env.openai.local`의 내부 인증키를 입력한다. `OPENAI_API_KEY`를 입력하지 않는다.
2. `GET /health/ready/v2`가 `200 {"status":"UP","provider":"openai"}`인지 확인한다. 이 검사는 설정·SQLite 접근·스키마만 확인하고 실제 모델을 호출하지 않는다. OpenAI 키의 유효성·크레딧·모델 접근권한·응답 품질까지 확인한 결과는 아니다.
3. `POST /v2/analyses`에서 아래 합성 본문을 사용한다. `[guid]::NewGuid().ToString()`으로 새 UUID를 만들고 본문의 `requestId`와 `X-Request-Id`에 **같은 값**을 넣는다. 다른 ID는 아래 합성 값을 유지해도 된다.

```json
{
  "requestId": "aaaaaaaa-0000-4000-8000-000000000901",
  "careSpaceId": "aaaaaaaa-0000-4000-8000-000000000902",
  "recordId": "aaaaaaaa-0000-4000-8000-000000000903",
  "content": "내일 오후 3시에 가상센터 2층 방문 일정이 있습니다.",
  "recordedAt": "2026-10-22T09:00:00+09:00",
  "timezone": "Asia/Seoul",
  "candidates": []
}
```

첫 실행은 유료 모델 호출 1회다. `200`과 `X-AI-Provider: openai`, 동일한 응답 `requestId`를 확인한다. 이 사례의 의미상 기대는 `CREATE / SCHEDULE`, `changes.scheduledDate: "2026-10-23"`, `changes.scheduledTime: "15:00"`, `changes.location: "가상센터 2층"`이다. `evidence`는 입력의 연속 부분 문자열이어야 한다. 표시 제목 등 자유 문장은 실행마다 달라질 수 있다. 형식이 유효한 `200`만으로 이 의미상 기대까지 만족했다고 판단하지 않는다.

4. **같은 ID와 같은 본문**을 다시 전송한다. `409 REQUEST_ALREADY_RECEIVED`가 기대값이며 모델을 다시 호출하지 않는다.
5. **같은 ID를 유지한 채** `content`의 `2층`만 `3층`으로 바꾼다. 다른 조건도 유효하면 `409 REQUEST_ID_REUSED`가 기대값이며 모델을 호출하지 않는다. 새 ID로 바꾸면 중복 검사가 아니라 추가 유료 분석이 된다.
6. 인증을 해제하고 보내면 `401 UNAUTHORIZED`, 유효한 헤더 UUID와 본문 UUID를 다르게 보내면 `400 REQUEST_ID_MISMATCH`, 필수 `content`를 제거하면 `422 INVALID_REQUEST`다. 이 요청들은 모델을 호출하지 않는다.

8000의 stub은 `200`이어도 `suggestions: []`만 반환한다. 실제 모델 품질 검사로 사용하지 않는다. 최초 등록 이후 타임아웃·모델 오류가 발생해도 수신 기록은 보존된다. 자동 재시도·v1 fallback은 없으며, 사용자가 새 분석을 원할 때만 새 `requestId`를 쓰고 원본 `recordedAt`은 유지한다.

## 3. BE 실행 환경에 맞는 연결

BE에는 AI 주소와 **그 서버의 내부 인증키만** 전달한다. `.env` 전체·OpenAI 키·HMAC 키·SQLite 파일은 전달하지 않는다. 내부 인증키는 별도의 안전한 경로로 공유한다. 이 안내를 실행한다고 BE 파일이 자동으로 변경되지는 않는다.

내부 호출은 `POST /v2/analyses`, `Content-Type: application/json`, `X-Internal-Api-Key`, `X-Request-Id`와 비압축 UTF-8 JSON 본문을 사용한다. 성공 본문은 `requestId`·`suggestions`, 오류 본문은 `code`·`message`이며 BE 공개 API의 `ApiResponse` 래퍼를 붙이지 않는다.

| BE 실행 위치 | 실제 모델 AI 주소의 예 | 전제 |
| --- | --- | --- |
| AI와 같은 Windows 호스트 | `http://127.0.0.1:8001` | 위 8001 서버 실행 |
| Docker 컨테이너, AI는 Windows 호스트 | `http://host.docker.internal:8001` | Docker Desktop 경로에서 접근 가능한 바인딩 또는 프록시·방화벽 설정 필요 |
| BE와 AI가 같은 Docker 네트워크 | `http://<AI 서비스명>:<컨테이너 포트>` | 실제 Compose 서비스명과 컨테이너 내부 포트 사용 |

컨테이너의 `127.0.0.1`은 컨테이너 자신이다. 위 기본 AI 명령은 호스트의 루프백에만 바인딩하므로, Docker 주소만 바꿔도 연결된다고 보장할 수 없다. 컨테이너 연결이 필요한 경우에만 접근 범위를 검토해 바인딩·프록시를 구성한다. BE PostgreSQL의 `5432`는 DB 포트이며 AI HTTP 주소에 사용하지 않는다.

현재 BE 코드에서 사용하는 외부 설정은 다음과 같다. 실제 BE 연동은 격리된 로컬·통합 환경에서 준비한다.

| BE 설정 | 확인할 값 |
| --- | --- |
| `AI_BASE_URL` | BE 위치에서 도달 가능한 AI 주소. `/v2/analyses` 경로는 붙이지 않음 |
| `AI_INTERNAL_API_KEY` | 해당 AI 서버와 같은 내부 인증키 |
| `ANALYSIS_CONTRACT_VERSION` | 검증할 새 실행에는 `v2`. 기본은 `v1`이며 revision 문자열을 넣지 않음 |
| `ANALYSIS_WORKER_ENABLED` | 실행기를 사용할 때 `true`. 기본 `false`이면 접수 후 `QUEUED` 유지 |
| `ANALYSIS_WORKER_CONCURRENCY` | BE 실행기 동시 수를 명시 |
| `ANALYSIS_WORKER_POLL_INTERVAL` | 대기 작업 확인 간격을 명시 |
| `ANALYSIS_WORKER_LEASE_DURATION` | AI 연결·응답 타임아웃 합보다 긴 기간을 명시. 현재 설정 기준 33초 초과 |
| `AI_V2_MAX_REQUEST_BYTES`, `AI_V2_MAX_RESPONSE_BYTES` | local 기본 1,048,576 / 524,288. 다른 프로필에서는 명시 |

worker 동시 수·확인 간격·lease의 운영값은 이 문서로 확정하지 않는다. worker를 켜면 이미 대기 중인 작업도 처리하므로, 유료 호출 전 대기 작업과 검증 범위를 확인한다. `AI_BASE_URL`과 내부 인증키는 BE의 v1·v2 클라이언트가 함께 사용한다. 설정 변경 전 기존 v1 실행에 대한 영향도 확인한다. 계약 버전은 접수 시 고정되어 과거 실행이 자동으로 v2가 되지 않는다.

## 4. BE 저장·조회까지 확인할 순서

BE 세션 인증과 참여 권한, PostgreSQL·마이그레이션, worker를 준비한 뒤 합성 데이터만 사용한다. 다음은 실행할 검증 항목이며, AI 단독 검사만으로 완료 처리하지 않는다.

1. `POST /api/spaces/{spaceId}/source-records`에 합성 `content`와 새 `clientRequestId`를 보내 원본 저장과 최초 분석 접수를 확인한다. FE의 `clientRequestId`와 BE→AI의 내부 `requestId`는 다른 식별자다.
2. 반환된 실행을 `GET /api/spaces/{spaceId}/source-records/{recordId}/analyses/{analysisId}`로 조회한다. `QUEUED → RUNNING → COMPLETED`와 `proposalIds`를 확인한다. 빠르게 끝나면 중간 상태를 직접 관찰하지 못할 수 있다.
3. `GET /api/spaces/{spaceId}/proposals`와 `GET /api/spaces/{spaceId}/proposals/{proposalId}`에서 작업·대상·버전·근거와 `currentValues`, `proposedValues`, `changedFields`, `clearFields`, `comparisonBasis`를 확인한다. 제안 저장만으로 CareItem이나 확정 답변이 자동 변경되면 안 된다.
4. `CREATE`, `UPDATE`, `RESOLVE`, `LINK_EVIDENCE`는 각각 필요한 후보를 준비해 확인한다. 실제 모델이 특정 제안을 내지 않았다는 이유로 계약 기대값을 바꾸지 않는다. 오류·동시 변경·부분 저장 차단의 결정적 검사는 공통 fixture와 BE의 가짜 AI 응답 테스트를 함께 사용한다.
5. 분석 실패 시 원본 저장이 유지되고 실행은 `FAILED`, 제안은 부분 저장되지 않는지 확인한다. 분석 조회 자체는 `200`이며 `data.failure`에 원인이 담긴다. AI 내부 오류 HTTP 상태가 공개 조회 HTTP 상태로 그대로 전달되는 것은 아니다.
6. 후보 버전 변경·근거 연결 제거·직접 입력 문서 무효화가 분석 중 또는 완료 후 발생하면 폐기·조회 차단 규칙을 확인한다. 삭제된 원본의 공개 분석 조회는 `404`이며 내부 `DISCARDED` 상태 검증과 구분한다. 공개 삭제 경로가 아직 없는 사례는 BE의 격리 통합 테스트로 확인한다.

실제 모델 성공·중복 검사와 별도로 v1 저장·조회 회귀도 유지한다. BE 테스트 절차와 공개 DTO 기준은 BE의 `docs/ai-contract-v2-execution.md` 및 실제 컨트롤러를 따른다.

## 5. 실패를 구분하는 기준

| AI HTTP / code | BE 통신 어댑터 실패 코드 |
| --- | --- |
| `401 UNAUTHORIZED`, `400 REQUEST_ID_MISMATCH`, `413 REQUEST_TOO_LARGE`, `422 INVALID_REQUEST`, `503 AI_NOT_CONFIGURED` | `AI_INTEGRATION_ERROR` |
| `409 REQUEST_ALREADY_RECEIVED` | `AI_DUPLICATE_REQUEST` |
| `409 REQUEST_ID_REUSED` | `AI_REQUEST_ID_REUSED` |
| `502 INVALID_AI_RESPONSE` | `AI_INVALID_RESPONSE` |
| `502 AI_UNAVAILABLE`, `503 AI_OVERLOADED` | `AI_SERVICE_UNAVAILABLE` |
| `503 RECEIPT_STORE_UNAVAILABLE`, `503 REQUEST_COMPARISON_UNAVAILABLE` | `AI_RECEIPT_STORE_UNAVAILABLE` |
| `504 AI_TIMEOUT` | `AI_TIMEOUT` |

요청 전송 바이트 상한과 디코딩 후 필드·UTF-16 문자열 상한은 동시에 적용된다. 초과한 입력을 자르거나 후보를 생략하지 않는다. BE가 응답 바이트 상한 초과를 발견하면 HTTP 상태와 관계없이 `AI_INVALID_RESPONSE`로 처리한다. HTTP/code 조합을 함께 검사하며 제공자 원본 오류는 노출하지 않는다. 전체 규칙은 [계약 13절·16절](ai-contract-v2.md)을 따른다.

AI 제공자 대기 기본은 20초, 전체 처리 예산은 26초이며 BE는 연결 3초·전체 HTTP 기한 30초다. 실제 모델이 항상 20초 안에 끝난다는 보장은 아니다. AI의 분석 슬롯 2개·대기열 없음은 프로세스 기준이고, 취소된 작업도 실제 종료까지 슬롯을 점유한다. 본문 수신 보호 한도와 분석 슬롯은 별개다. 과부하를 자동 재시도나 새 ID 발급으로 우회하지 않는다.

## 6. 합성 데이터로 모델 품질 재평가

계약 fixture 테스트와 별도로 회귀용 사례와 독립 사례를 평가한다. 기대값은 호출 전에 고정하고, 모델 결과에 맞춰 완화하지 않는다. 기존 `holdout` 12개는 최초 평가 이후 프롬프트 보완에 사용하여 현재 `regression-after-feedback`으로 구분한다. 새 독립 사례는 `change-holdout` 3개이며 프롬프트·실제 응답을 읽지 않은 별도 작성자가 기대값을 고정했다. 다음 명령은 각각 명시한 수 이내의 **유료 호출**을 수행한다.

```powershell
uv run --locked python -m scripts.evaluate_v2 --live --suite regression --max-calls 19 --output test-results/v2-regression-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite holdout --max-calls 12 --output test-results/v2-holdout-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite change-intent --max-calls 6 --output test-results/v2-change-regression-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite change-holdout --max-calls 3 --output test-results/v2-change-holdout-001.json
```

평가기는 별도 임시 SQLite를 사용하므로 실행 중인 서버의 수신 기록을 지우지 않는다. 매 평가마다 새 출력 경로를 사용한다. 기존 결과나 중단 시 예약된 파일을 자동으로 덮어쓰지 않는다. 모델·reasoning·처리 등급·데이터·프롬프트·채점 기준의 revision/hash와 계약 통과율·의미상 정확도·시간·토큰 사용량을 함께 확인한다. 소수의 순차 합성 평가 통과를 운영 품질이나 동시 부하 보장으로 해석하지 않는다.
