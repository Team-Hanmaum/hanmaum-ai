# v2 로컬 실행과 BE 연동 확인

[v2 계약](ai-contract-v2.md)과 공통 fixture의 기준은 `v2-20261021.1`이다. 합성 모델 평가 데이터는 별도 revision으로 관리한다.

## 1. 로컬 설정과 서버 실행

[README의 v2 실행 절차](../README.md#v2-http-실행)대로 `.env`를 준비한다. v2 stub과 OpenAI 모두 초기화된 AI 전용 SQLite와 별도 HMAC 키가 필요하다. 같은 저장소를 사용하는 서버는 요청 ID를 공유하므로 검증마다 새 ID를 사용한다.

- **8000 stub** — `.env`의 `APP_ENV=local`과 `AI_V2_PROVIDER=stub`으로 실행한다.
- **8001 OpenAI** — `.env`에 `OPENAI_API_KEY`를 설정한다. Git에서 제외되는 `.env.openai.local`에는 `AI_V2_PROVIDER=openai`와 별도 무작위 `AI_INTERNAL_API_KEY`를 둔다. 모델 기본 설정은 `gpt-6-luna`·`low`·`default`다.

```powershell
# 각 명령은 별도 터미널에서 실행
uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
uv run --locked uvicorn app.main:create_app --factory --env-file .env.openai.local --reload --host 127.0.0.1 --port 8001
```

프로세스에 이미 설정된 환경 변수가 파일보다 우선한다. 각 터미널에서 `Ctrl+C`로 종료한다.

## 2. Swagger에서 성공과 중복 확인

1. 로컬 `/docs`의 **Authorize**에 해당 서버의 `AI_INTERNAL_API_KEY`를 입력한다.
2. `GET /health/ready/v2`의 `200`과 `provider`를 확인한다. 설정과 저장소를 검사하며 모델 키 권한·잔액·품질은 별도 확인 대상이다.
3. `POST /v2/analyses`에서 Swagger 예시 또는 [합성 요청](../tests/fixtures/v2/payloads/requests/base.json)을 사용한다. `X-Request-Id`와 본문의 `requestId`를 같은 새 UUID로 바꾼다. 실제 모델을 확인할 때는 후보를 비우고 아래와 같이 원문과 기록 시각을 설정할 수 있다.

```json
{
  "content": "내일 오후 3시에 가상센터 2층 방문 일정이 있습니다.",
  "recordedAt": "2026-10-22T09:00:00+09:00",
  "timezone": "Asia/Seoul",
  "candidates": []
}
```

위 조각은 합성 요청의 해당 필드에 적용하고 `careSpaceId`·`recordId`는 유지한다. stub은 `200`과 `suggestions: []`를 반환한다. OpenAI 모드의 첫 유효 요청은 유료 호출 1회이며, 예상 제안은 `CREATE / SCHEDULE`, 날짜 `2026-10-23`, 시각 `15:00`, 장소 `가상센터 2층`이다.

같은 본문을 다시 보내면 `409 REQUEST_ALREADY_RECEIVED`, 같은 ID에 다른 유효 본문을 보내면 `409 REQUEST_ID_REUSED`다. 인증 없음은 `401`, 유효한 헤더 ID와 본문 ID 불일치는 `400`, 필수 필드 누락은 `422`를 확인한다.

## 3. BE 실행 환경에 맞는 연결

| BE 설정 | 적용 |
| --- | --- |
| `AI_BASE_URL` | 같은 장비에서 직접 실행하면 `http://127.0.0.1:8001`. 운영은 `https://hanmaum-ai:8443`. 엔드포인트 경로는 붙이지 않는다. |
| `AI_INTERNAL_API_KEY` | AI 서버와 같은 내부 키 |
| `ANALYSIS_CONTRACT_VERSION` | 새 검증 실행은 `v2`. BE 앱 기본값은 `v1`, 운영 Compose는 `v2`. 접수된 실행은 기존 버전을 유지한다. |
| `ANALYSIS_WORKER_ENABLED` | 실행 검증 시 활성화. 기본은 `false`이며 활성화하면 이전 대기 작업도 처리한다. |
| `ANALYSIS_WORKER_CONCURRENCY`, `ANALYSIS_WORKER_POLL_INTERVAL`, `ANALYSIS_WORKER_LEASE_DURATION` | 동시 수·조회 간격·lease를 명시한다. lease는 BE 연결·응답 타임아웃 합인 33초보다 길어야 한다. |
| `AI_V2_MAX_REQUEST_BYTES`, `AI_V2_MAX_RESPONSE_BYTES` | 각각 1,048,576·524,288바이트. BE 프로필 설정을 확인한다. |

호스트 간 또는 공유 네트워크는 인증서 체인·호스트명을 검증하는 HTTPS나 인증된 암호화 터널을 사용한다. 평문 HTTP는 신뢰하는 같은 개발 장비의 로컬 경로에 한정한다. Docker 안의 루프백은 컨테이너 자신이며, 호스트 AI가 루프백에만 바인딩돼 있으면 `host.docker.internal` 주소만 바꿔 연결할 수 없다. BE에 전달하는 값은 AI 주소와 내부 인증키다. 운영 네트워크와 CA는 [배포 안내](deployment.md)를 따른다.

## 4. BE 저장·조회까지 확인할 순서

BE 세션·공간 권한·DB·워커를 준비하고 합성 데이터로 확인한다.

1. `POST /api/spaces/{spaceId}/source-records`로 원본을 저장하고 분석 ID를 받는다. FE `clientRequestId`와 AI 내부 `requestId`는 별개다.
2. `GET /api/spaces/{spaceId}/source-records/{recordId}/analyses/{analysisId}`에서 최종 상태와 `proposalIds`를 확인한다.
3. `GET /api/spaces/{spaceId}/proposals/{proposalId}`에서 작업·근거·현재 값·제안 값·삭제 필드를 확인한다. 항목이나 답변은 사용자 반영 단계에서 확정한다.
4. 실패 시 원본 보존·제안 부분 저장 없음·`data.failure`를 확인한다. 삭제 후 분석 조회는 `404`다. 원본 삭제는 `POST /api/spaces/{spaceId}/source-records/{recordId}/deletions`를 사용하고, 분석 중 입력 무효화 경쟁은 BE 격리 테스트로 재현한다.

AI HTTP 성공은 BE 저장·조회 검증과 별도로 기록한다. v1 회귀와 전체 실행 검증은 [BE 실행 계약](https://github.com/Team-Hanmaum/hanmaum-backend/blob/dev/docs/ai-contract-v2-execution.md)을 참고한다.

## 5. 실패를 구분하는 기준

HTTP와 오류 코드를 쌍으로 확인한다. 전체 목록은 [v2 오류 매핑](ai-contract-v2.md#13-ai-오류와-be-실패-코드)에 있다. BE에서 발견한 전송 전 요청 초과는 호출 없이 `AI_INTEGRATION_ERROR`, 수신 중 응답 바이트 초과는 `AI_INVALID_RESPONSE`다. 제한 초과 입력을 자르거나 자동 재호출하지 않는다. AI의 제공자 대기는 기본 20초, 전체 처리 예산은 26초다. BE의 연결은 3초, 전체 HTTP 기한은 30초다.

## 6. 합성 데이터로 모델 품질 재평가

아래 명령은 **유료 호출**이다. 계약 fixture와 별도로 의미 정확도·근거·지연·토큰 사용량을 기록한다. 현재 `facts-v2-20261023.2` 채점은 허용된 변경 필드만 인정한다. 이전 `.1`의 실제 평가 점수는 새 기준의 결과와 구분한다. `holdout`은 프롬프트 보완에 사용한 회귀 자료, `change-holdout`은 독립 평가 자료다.

```powershell
uv run --locked python -m scripts.evaluate_v2 --live --suite regression --max-calls 19 --output test-results/v2-regression-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite holdout --max-calls 12 --output test-results/v2-holdout-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite change-intent --max-calls 6 --output test-results/v2-change-regression-001.json
uv run --locked python -m scripts.evaluate_v2 --live --suite change-holdout --max-calls 3 --output test-results/v2-change-holdout-001.json
```

평가기의 SQLite는 실행 중인 서버와 분리되어 있다. 매번 새 출력 경로를 지정하고 모델·프롬프트·데이터·채점 기준이 같은 결과끼리 비교한다.
