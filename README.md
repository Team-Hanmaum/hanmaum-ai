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
이 레포는 DB를 직접 읽거나 수정하지 않습니다.
분석 계약과 제약은 [API 계약 문서](docs/ai-contract.md)를 참고합니다.

## 개발 기준

- 개발 기준 브랜치: `dev`
- 배포 기준 브랜치: `main`
- 작업 브랜치: `타입/이슈번호-기능명`
- 커밋: `타입: 변경 내용 (#이슈번호)` 및 상세 본문
- PR: `dev` 대상으로 생성하고 팀원 2명 이상의 승인 후 병합

분석 결과는 사용자 확인 전 제안이며, 사용자 권한과 DB 변경은 Spring에서 담당합니다.
