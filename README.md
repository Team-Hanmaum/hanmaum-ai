# 한마음 AI

Spring 백엔드의 돌봄 기록 분석 요청을 처리하는 FastAPI 서버입니다.

Python 3.13.15, FastAPI, Pydantic, uv, Ruff, pytest를 사용합니다. 의존성 버전은
`uv.lock`으로 공유하고, 프로젝트별 `.venv`에서 실행합니다.

현재는 서버 연동을 확인하는 **stub(시험용 응답)** 단계입니다. 모델 API를 호출하거나 기록을
분석하지 않으며, 정상 요청에는 `suggestions: []`를 반환합니다. 실제 LLM 제공자·모델·프롬프트는
후속 작업에서 연결합니다.

## 처음 실행하기

### 1. uv 설치

Windows PowerShell에서 한 번 실행한 뒤 터미널을 새로 엽니다.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS/Linux에서는 [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를
따릅니다. 이 프로젝트는 uv 0.12.19 이상이 필요합니다.

### 2. 프로젝트 준비

아래는 Git Bash 기준입니다. 초기 설정 PR이 dev에 병합된 뒤 실행합니다.

```bash
git switch dev
git pull origin dev
uv sync --locked
cp -n .env.example .env
git config core.hooksPath .githooks
```

- `uv sync --locked`: 정해진 Python과 의존성 설치. `.python-version`에 지정한 Python이 없으면
  uv가 다운로드하며, 다른 프로젝트의 전역 Python을 바꾸지 않습니다.
- `cp -n`: 기존 `.env`가 있으면 덮어쓰지 않습니다.
- PowerShell에서는 복사 명령 대신
  `if (!(Test-Path .env)) { Copy-Item .env.example .env }`를 사용합니다.
- `.env`, `.venv`, 캐시는 Git에 올라가지 않습니다.
- VS Code는 이 레포 폴더를 열고 권장 확장을 설치합니다. Python 인터프리터는
  프로젝트의 `.venv`를 선택합니다.

### 3. 개발 서버 시작

```bash
uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

- 상태 확인: [http://localhost:8000/health](http://localhost:8000/health)
- 분석 준비 확인: [http://localhost:8000/health/ready](http://localhost:8000/health/ready)
- API 문서: [http://localhost:8000/docs](http://localhost:8000/docs)
- 종료: 해당 터미널에서 `Ctrl+C`

이 실행 방식에는 Docker나 PostgreSQL이 필요하지 않습니다.

## 다음 개발부터

작업 중인 변경 사항을 정리한 뒤 개발 브랜치를 갱신합니다.

```bash
git switch dev
git pull origin dev
uv sync --locked
uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

`uv sync --locked`는 프론트의 `pnpm i --frozen-lockfile`에 해당하는 역할입니다.
다른 팀원이 의존성을 변경했을 때도 같은 잠금 파일을 기준으로 맞춥니다.
새 패키지는 `uv add 패키지명`, 개발 도구는 `uv add --dev 패키지명`으로 추가하고
`pyproject.toml`과 `uv.lock`을 함께 커밋합니다.

## 환경 변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `APP_ENV` | `local` | `local`, `test`, `prod` 중 하나 |
| `AI_PROVIDER` | `disabled` | `stub`은 로컬·테스트 전용, `disabled`는 분석 차단 |
| `AI_INTERNAL_API_KEY` | 로컬 예시 키 | Spring과 AI가 공유하는 내부 호출 인증키 |
| `ANALYSIS_TIMEOUT_SECONDS` | `20` | 분석 대기 시간, 0초 초과·25초 이하 |

`.env.example`을 복사하면 `AI_PROVIDER=stub`이 적용됩니다.
`.env`도 환경 변수도 없으면 분석은 비활성화되며 503을 반환합니다.
OS 환경 변수가 `.env`보다 우선합니다. 설정 변경 후 서버를 재시작합니다.

내부 키는 LLM API 키와 다른 값이며, 브라우저에 전달하지 않습니다.
로컬 연동 시 백엔드와 AI의 `AI_INTERNAL_API_KEY`를 동일하게 설정합니다.
운영 환경에서는 별도로 생성한 충분히 긴 무작위 키를 환경 변수로 주입하며,
32자 미만 키·로컬 예시 키·stub 모드는 시작 시 거부합니다.

## Spring 연동 확인

Spring 쪽 환경 변수는 아래처럼 설정합니다. 변경 후 Spring도 재시작합니다.

```dotenv
AI_BASE_URL=http://localhost:8000
AI_INTERNAL_API_KEY=local-development-only
```

AI 쪽 `.env`의 내부 키를 바꿨다면 Spring에도 같은 값을 넣습니다.
Spring을 컨테이너로 실행할 때의 `localhost`는 그 컨테이너 자신이므로,
배포에서는 동일한 Docker 네트워크의 AI 서비스 주소를 사용해야 합니다.

Git Bash에서 직접 요청을 확인할 수 있습니다. 아래 키는 로컬 예시 값입니다.

```bash
curl -i http://localhost:8000/v1/analyses \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Api-Key: local-development-only' \
  -H 'X-Request-Id: 00000000-0000-0000-0000-000000000001' \
  --data-binary @tests/fixtures/analysis-request.json
```

stub 응답:

```json
{
  "requestId": "00000000-0000-0000-0000-000000000001",
  "suggestions": []
}
```

응답 헤더의 `X-AI-Provider: stub`으로 시험용 응답임을 확인할 수 있습니다.
비어 있는 결과가 실제 분석 완료를 뜻하지는 않습니다.

## v2 HTTP 실행

`POST /v2/analyses`는 `v2-20261021.1` 계약을 구현합니다. v1 설정은 그대로 유지하고
v2를 별도로 활성화합니다. 기본 `AI_V2_PROVIDER=disabled`에서는 503 AI_NOT_CONFIGURED이며,
`stub`은 로컬·테스트 전용으로 빈 제안을 반환합니다. 실제 LLM 연결과 분석 품질 평가는 후속입니다.

1. AI 전용 영속 디렉터리를 준비하고 **최초 한 번만** 저장소를 초기화합니다.
   `uv run --locked python -m app.v2.initialize_receipts C:/dev/Hanmaum/ai-data/receipts.sqlite3`
   상위 디렉터리는 먼저 생성해야 합니다. 기존 정상 DB에 실행하면 기록을 유지하지만,
   파일 유실을 복구하려고 빈 DB를 초기화하면 이전 요청의 중복 차단 근거를 잃습니다.
2. 인증키와 별개로 무작위 HMAC 키를 생성하여 비밀 설정에 보관합니다. 키는 최소 32바이트이며,
   `.env`의 `AI_V2_HMAC_KEYS`에 `{"local-1":"별도로 생성한 키"}` 형식으로 설정합니다.
   같은 버전 이름의 키 값을 바꾸거나 기존 기록에 필요한 이전 키를 제거하지 않습니다.
3. `AI_V2_ACTIVE_HMAC_KEY_VERSION=local-1`, 절대 경로인 `AI_V2_RECEIPT_DB_PATH`,
   `AI_V2_PROVIDER=stub`을 설정하고 서버를 재시작합니다. DB·키를 서버가 자동 생성하지 않습니다.
4. 기존 `/health/ready`는 v1 상태이며, v2는 내부 키를 포함해 `GET /health/ready/v2`로 확인합니다.
   이 확인은 저장소 접근·스키마만 검사하고 기록을 생성하거나 제공자를 호출하지 않습니다.

```bash
curl -i http://localhost:8000/v2/analyses \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Api-Key: local-development-only' \
  -H 'X-Request-Id: aaaaaaaa-0000-4000-8000-000000000001' \
  --data-binary @tests/fixtures/v2/payloads/requests/base.json
```

동일 요청을 다시 보내면 409 REQUEST_ALREADY_RECEIVED입니다. 명시적인 새 실행은 새 requestId를
헤더와 본문에 함께 사용하고 원본 recordedAt은 유지합니다. 자동 재시도·v1 fallback은 없습니다.

Swagger에서는 [로컬 API 문서](http://localhost:8000/docs)를 열고 다음 순서로 확인합니다.

1. **Authorize**에 `.env`의 `AI_INTERNAL_API_KEY`를 입력합니다. LLM API 키가 아닙니다.
2. `GET /health/ready/v2` → **Try it out → Execute**로 준비 상태를 확인합니다.
   기본 비활성화 상태는 503 AI_NOT_CONFIGURED, 저장소까지 준비된 stub은 200입니다.
3. `POST /v2/analyses` → **Try it out**에서 `X-Request-Id`를 입력하고 요청 예시를 선택합니다.
   `minimal`은 후보 없는 요청, `question`은 질문 후보와 근거가 있는 요청입니다.
   본문의 `requestId`와 헤더는 같은 UUID여야 합니다. 예시 ID를 이미 사용했다면 두 값을
   함께 새 UUID로 변경해야 새 실행이 됩니다. `recordedAt`은 원본 시점을 유지합니다.
4. stub 활성화 시 첫 요청의 200·빈 제안, 동일 요청의 409, 같은 ID에 다른 유효 본문의
   409 REQUEST_ID_REUSED, 헤더 ID 불일치의 400, 필수 content 누락의 422를 확인합니다.
   인증 해제 후에는 401입니다. 기본 비활성화 상태에서도 인증·요청 검증은 적용됩니다.

Swagger의 **Schema**는 v2 중첩 후보·근거·응답 필드를 설명하고 **Responses**는 상태별 오류
코드와 `code`·`message` 예시를 제공합니다. 200의 질문 답변 예시는 계약 형식 설명이며 현재
stub이 생성하는 결과는 아닙니다. 정확한 크기 경계·동시성·취소는 자동 HTTP 테스트로 확인합니다.
Swagger 명세는 스트림 수신과 별도로 구성하여 본문 선행 버퍼링이나 검증 순서를 바꾸지 않습니다.

| AI 전용 설정 | 기본값 / 상한 | 적용 |
| --- | --- | --- |
| `AI_V2_PROVIDER_TIMEOUT_SECONDS` | 20초 / 25초 | 제공자 대기; 남은 전체 예산으로 추가 제한 |
| `AI_V2_TOTAL_TIMEOUT_SECONDS` | 26초 / 26초 | 본문 수신·파싱·저장소·제공자·검증·직렬화의 전체 처리 기한 |
| `AI_V2_BODY_TIMEOUT_SECONDS` | 2초 / 2초 | 본문 수신 |
| `AI_V2_WORKER_TIMEOUT_SECONDS` | 1초 / 1초 | 파싱·저장소·검증·직렬화 각 워커 대기 |
| `AI_V2_STORE_LOCK_TIMEOUT_SECONDS` | 0.25초 / 0.5초 | SQLite 잠금 대기; 워커 기한보다 작아야 함 |

제공자 슬롯은 프로세스당 2개, 수신부터 응답 전송까지의 진입 슬롯은 4개이며 대기열이 없습니다.
CPU 작업과 저장소 작업도 각각 4개까지만 제출합니다. 응답 전송 대기는 합계 0.5초로 제한합니다.
전체 처리 26초 + 전송 0.5초 + BE 연결 3초를 BE 전체 HTTP 기한 30초 안에 배치했습니다.
제공자 대기는 검증·직렬화 예산을 남기며, 타임아웃이 나도 실제 작업 종료까지 슬롯을 유지합니다.
기존 기록은 제공자 슬롯 부족보다 먼저 판정하고, 신규 요청은 슬롯 확보 후 원자적으로 재확인·등록합니다.
진입 슬롯이 모두 사용 중이면 본문을 읽기 전에 503 AI_OVERLOADED로 거부합니다.
응답 전송이 막히거나 연결이 끊어지면 두 번째 오류 본문을 붙이지 않고 전송을 중단합니다.

이는 초기 로컬·통합 실행 설정입니다. 프로세스·인스턴스 수에 따라 총 동시 실행 수가 늘어납니다.
스레드의 강제 종료나 프로세스 전체 메모리 상한을 보장하지 않으며, 실제 제공자 어댑터의
스트림 읽기·토큰·출력 한도, 서버/프록시 연결 제한과 운영 자원 예산은 실제 모델 연결·배포에서 검증합니다.
컨테이너 사용 시 DB를 영속 볼륨에 두고 같은 호스트의 모든 프로세스가 같은 파일을 사용해야 합니다.
BE 업무 DB에 연결하지 않으며, 저장소 보관·정리 정책 확정 전 자동 삭제는 없습니다.

## 검사와 테스트

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
```

자동 정리가 필요하면 `uv run --locked ruff check --fix .`와
`uv run --locked ruff format .`를 실행합니다.
`.githooks/pre-commit`은 Python·의존성 관련 변경을 커밋할 때 린트와 형식을 검사합니다.
테스트는 위 명령으로 실행합니다. 테스트에는 실제 LLM 호출과 API 키가 필요하지 않습니다.

### GitHub Actions CI

[AI CI](.github/workflows/ci.yml)는 모든 대상 브랜치의 PR 생성·코드 갱신·재오픈과
`dev`·`main` push에서 `Lint and test` job을 실행합니다. 같은 PR 또는 ref의 이전 실행은
취소하며, job 제한 시간은 10분입니다.

Ubuntu 24.04에서 `.python-version`의 Python과 uv 0.12.19를 사용합니다.
의존성은 `uv.lock`으로 고정하고, uv 캐시는 PR에서 복원하며 `dev`·`main` push에서 저장합니다.
Action은 전체 커밋 SHA로 고정하며 저장소 내용 읽기 권한만 사용합니다.

검사 순서와 로컬 재현 명령:

```bash
uv python install
uv sync --locked --dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/fixture_manifest.py
uv run --locked pytest --junitxml=test-results/pytest.xml
```

- fixture 검사는 manifest와 실제 파일 목록·해시·사례 메타데이터를 비교합니다.
  파일 목록은 운영체제와 무관한 정렬로 비교하여 Windows·Linux의 경로 정렬 차이를 제거합니다.
  `--write`로 기준을 자동 갱신하지 않습니다. 전체 pytest에도 관련 검사가 포함되지만
  fixture 문제를 먼저 구분할 수 있도록 별도 step을 유지합니다.
- pytest는 기존 v1, v2 계약, 공통 fixture, 수신 기록·리뷰 회귀를 모두 실행합니다.
  가짜 제공자와 임시 SQLite를 사용하며 실제 LLM 키·GPU·BE DB·별도 DB 서비스가 필요하지 않습니다.
- pytest가 생성한 `test-results/pytest.xml`은 테스트 실패 시에도 실행이 취소되지 않았다면
  `ai-test-results` artifact로 업로드하여 7일간 보관합니다. 테스트 실행 전 실패로 파일이
  생성되지 않았다면 업로드를 생략합니다. 로컬 결과 디렉터리는 Git에서 제외합니다.
- CI는 AI 저장소 내부의 검증입니다. BE 복사본 동기화·BE 통합·실제 모델 품질·배포 검증과
  구분합니다. 머지 차단에 사용할 필수 검사 지정은 GitHub 브랜치 보호·ruleset의 별도 설정입니다.

## Docker로 실행하기

Docker Desktop을 실행한 상태에서 아래 명령을 사용합니다.
로컬 Python 개발 대신 컨테이너로 실행하고 싶을 때나 배포 이미지를 확인할 때 사용합니다.
같은 8000 포트를 사용 중인 로컬 서버는 먼저 종료합니다.

```bash
docker build -t hanmaum-ai:local .
docker run -d --name hanmaum-ai-local \
  --env-file .env \
  -p 127.0.0.1:8000:8000 \
  hanmaum-ai:local
docker logs -f hanmaum-ai-local
```

- `-d`: 컨테이너를 백그라운드에서 실행합니다.
- `Ctrl+C`로 로그 보기를 끝내도 백그라운드 컨테이너는 계속 실행됩니다.
- 종료·다시 시작: `docker stop hanmaum-ai-local`, `docker start hanmaum-ai-local`
- 코드나 환경 변수를 바꾼 뒤에는 컨테이너를 삭제하고 다시 생성합니다.
  코드 변경 시 이미지도 다시 빌드합니다.

```bash
docker stop hanmaum-ai-local
docker rm hanmaum-ai-local
docker build -t hanmaum-ai:local .
docker run -d --name hanmaum-ai-local --env-file .env -p 127.0.0.1:8000:8000 hanmaum-ai:local
```

이미지에는 앱과 실행 의존성만 들어가며 `.env`, 테스트, 개발 도구를 포함하지 않습니다.
일반 사용자 UID 10001로 실행합니다. 이미지 기본값은 `APP_ENV=prod`,
`AI_PROVIDER=disabled`이며 위 로컬 실행에서는 `.env` 값이 이를 덮어씁니다.
운영 이미지 실행에는 별도의 내부 키 주입이 필요합니다.

Docker의 HEALTHCHECK는 서버 생존 여부인 `/health`를 확인합니다.
분석 요청을 받을 준비가 되었는지는 `/health/ready`로 별도 확인합니다.
현재 운영 모드는 분석이 비활성화되어 readiness가 503이며,
실제 모델 연결·평가 전에는 서비스의 AI 기능을 운영에 공개하지 않습니다.

Windows에서 개발을 마치고 WSL 메모리까지 반환하고 싶다면 컨테이너를 중지한 뒤
Docker Desktop을 트레이 메뉴의 **Quit Docker Desktop** 또는 `docker desktop stop`으로
먼저 종료합니다. 종료가 완료되고 다른 WSL 작업도 없을 때 `wsl --shutdown`을 실행합니다.
Docker가 켜진 채 WSL이나 관련 프로세스를 강제 종료하면 다음 시작에 문제가 생길 수 있습니다.
Docker Desktop의 시작 오류 창은 FastAPI 코드 오류와 구분해서 확인합니다.

## 구조와 역할

```text
app/
  main.py         앱 생성
  config.py       환경 변수
  api.py          상태 확인·분석 API
  schemas.py      요청·응답 계약
  v2/
    schemas.py    v2 전용 요청·응답 모델과 필드·제안 조합 검증
    validation.py 원문 JSON 파싱과 요청·결과 사이의 계약 검증
    comparison.py 동일 본문 정규화·버전별 HMAC 비교
    receipts.py   SQLite 수신 기록·원자적 등록·중복 차단
    services.py   내부 v2 호출 제어·타임아웃·원본 기준 응답 검증
    api.py        v2 분석·준비 상태 HTTP 진입점
    limits.py     바이트·UTF-16·컬렉션 공통 상한
    resources.py  대기열 없는 진입·작업 슬롯과 워커 수명 관리
    transport.py  응답 전송 시간 제한과 연결 종료 정리
    config.py     v2 전용 설정
    runtime.py    수신 저장소·가짜 제공자 조립
  security.py     내부 키 인증
  services.py     타임아웃·분석 결과 검증
  providers.py    분석 제공자 인터페이스·stub
  errors.py       오류 응답
tests/
  fixtures/       백엔드 계약의 합성 JSON 예시
docs/
  ai-contract.md  API 규칙과 후속 구현 범위
```

Spring은 로그인, 공간 권한, 원문·제안 저장, 사용자 확인, DB 변경을 담당합니다.
AI는 Spring이 보낸 원문과 후보를 바탕으로 제안만 반환합니다.
AI는 BE 업무 DB를 직접 읽거나 수정하지 않습니다. v2 수신 기록은 AI 전용 SQLite 파일에 관리합니다.
분석 계약과 제약은 [API 계약 문서](docs/ai-contract.md)를 참고합니다.

v2는 [확정 계약](docs/ai-contract-v2.md)의 **v2-20261021.1**을 기준으로 HTTP API,
크기 제한·오류·수신 기록·HMAC·중복/과부하 우선순위·동시성 제어를 구현했습니다.
기존 v1과 [공통 fixture](tests/fixtures/v2/README.md)의 모든 원문·revision·manifest는 유지합니다.
fixture 내부의 실행 범위 표기는 발행 당시 상태이며, 현재 구현 범위는 아래와 계약 문서에서 관리합니다.
실제 모델 연결 및 BE 실행·저장·조회 통합은 후속 작업입니다.

공통 데이터의 파일 목록·SHA-256·revision 및 사례 일관성을 확인합니다.

```bash
uv run --locked python scripts/fixture_manifest.py
uv run --locked python -m scripts.fixture_payloads
uv run --locked pytest tests/test_v2_fixture_bundle.py
uv run --locked pytest tests/test_v2_fixture_revision.py
```

생성 도구의 `--write-metadata`는 같은 디렉터리의 임시 파일을 완전히 쓴 뒤
`generated.json`을 교체합니다. `--export`는 임시 디렉터리에 모든 원문을 준비·검증한 뒤
지정 경로로 이동하므로 중간 생성·쓰기 실패 시 최종 경로에 일부 파일만 남기지 않습니다.
처리 가능한 오류·인터럽트는 임시 출력을 정리하고, 강제 프로세스 종료 시에는 숨김 임시 경로가
남을 수 있습니다. 기존 출력 경로는 덮어쓰지 않으며 같은 경로를 대상으로 한 병렬 실행은 지원하지 않습니다.
두 옵션을 함께 사용해도 메타데이터 파일과 내보내기 디렉터리 전체가 하나의 트랜잭션은 아닙니다.

실제 v2 계약 코드의 판정은 별도 테스트로 확인합니다.

```bash
uv run --locked pytest tests/test_v2_contract.py
```

`app.v2.validation.parse_request(raw)`는 UTF-8 원문 JSON에서 중복 키와 시간 정밀도를
검사하고 v2 요청 모델을 반환합니다. 요청 본문을 미리 JSON 객체로 바꾸면 중복 키를 잃으므로
이 진입점에 원문 바이트를 전달합니다.
반환된 요청은 후보·현재 값·근거까지 읽기 전용이며 후보·근거 배열은 내부적으로 tuple로 보존합니다.
JSON 직렬화에서는 기존 배열 형식이 유지됩니다. 제공자가 입력을 가공해야 하면
`request.model_dump(mode="json")`으로 별도 사본을 전달하고, 응답 검증에는 최초 요청을 유지합니다.
`validate_response(request, payload)`는 제공자 결과의 원문 JSON 또는 객체를 받아
필드·작업 조합과 대상·버전·근거 참조를 검증하고, 하나라도 위반하면 전체 응답을 거부합니다.
요청 바이트 초과는 `AnalysisFailure(413, REQUEST_TOO_LARGE)`, 그 외 요청 위반은
`AnalysisFailure(422, INVALID_REQUEST)`, 결과 위반은
`AnalysisFailure(502, INVALID_AI_RESPONSE)`로 구분하며 원문을 오류 메시지나 로그에 넣지 않습니다.

`tests/test_v2_http.py`는 A-* 146개와 L-* 경계값 96개를 실제 FastAPI HTTP 경로에 연결해
상태·코드·응답·제공자 호출 수·수신 기록을 확인합니다. L-* 측정 전용 6개는 별도 생성 검증입니다.
B-* 기존 29개는 SQLite·내부 서비스로, B-uncomparable-record는 503 HTTP 응답으로 검증합니다.
H-* 25개 중 AI 담당 14개는 HTTP·경쟁·취소·슬롯 유지·청크 수신 테스트이며,
BE 담당 11개와 C-* 25개의 실행·저장·조회 통합은 BE 검증 범위입니다.
`tests/test_v2_http_resources.py`는 느린 수신·전송·워커, 전체 기한, 연결 종료, 설정을 보완 검증합니다.
정확한 caseId 구분과 책임은 [v2 계약의 구현 범위](docs/ai-contract-v2.md#현재-ai-구현과-테스트-범위)에 정리했습니다.
fixture 검사나 모델 검증 통과는 실제 HTTP·호출 횟수·DB 통합·모델 품질 검증 완료를 의미하지 않습니다.
HTTP는 Content-Length 유무와 무관하게 실제 읽은 요청 바이트를 제한합니다. 문자열은
JSON 디코딩 후 타입 변환 전에 UTF-16 단위로 검사하고, 제공자 원문·최종 직렬화 응답 모두 검증합니다.
스키마 클래스의 직접 생성은 수신 제한의 대체 경로가 아닙니다. 외부 요청은 HTTP 진입점을 사용합니다.

수신 기록 구현은 같은 호스트의 여러 프로세스가 하나의 로컬 SQLite 파일을 공유하는 범위입니다.
DB·HMAC 키는 v2 활성화 시에만 설정하며 기존 v1 실행에는 필요하지 않습니다.
기록 유실 시 자동으로 빈 DB를 만들지 않고, 자동 만료·삭제도 아직 도입하지 않았습니다.
비교 불가는 503 REQUEST_COMPARISON_UNAVAILABLE로 반환합니다. 보관·정리 정책은 미정입니다.
설계·초기화·키 관리·테스트 경계는 [v2 수신 기록 문서](docs/v2-receipts.md)를 참고합니다.

```bash
uv run --locked pytest tests/test_v2_receipts.py
```

BE에는 동일 revision의 `tests/fixtures/v2/` 전체를 전달합니다. 사례 형식, 원문 JSON 전송,
동시성·저장·조회 시나리오의 실행 책임과 동기화 방법은 fixture README에 정리되어 있습니다.

## 개발 기준

- 개발 기준 브랜치: `dev`
- 배포 기준 브랜치: `main`
- 작업 브랜치: `타입/이슈번호-기능명`
- 커밋: `타입: 변경 내용 (#이슈번호)` 및 상세 본문
- PR: `dev` 대상으로 생성하고 팀원 1명 이상의 승인과 필수 검사 충족 후 병합

이슈·브랜치·커밋·PR 및 AI 개발 세부 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.

분석 결과는 사용자 확인 전 제안이며, 사용자 권한과 DB 변경은 Spring에서 담당합니다.
