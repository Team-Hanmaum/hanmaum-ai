<p align="center">
  <img src="https://raw.githubusercontent.com/Team-Hanmaum/hanmaum-frontend/dev/public/icons/pwa-192.png" width="96" alt="흰 배경의 한마음 로고" />
</p>

<h1 align="center">한마음 · AI</h1>

<p align="center">
  <strong>흩어진 소식은 하나로, 돌봄은 한마음으로</strong><br />
  가족이 남긴 소식을 AI가 정리하고, 확인한 일정과 할 일을 함께 관리하는 모바일 웹 서비스입니다.
</p>

<p align="center">
  <a href="https://hanmaum.site">서비스 바로가기</a> ·
  <a href="https://github.com/Team-Hanmaum/hanmaum-frontend">Frontend</a> ·
  <a href="https://github.com/Team-Hanmaum/hanmaum-backend">Backend</a> ·
  <a href="https://github.com/Team-Hanmaum/hanmaum-ai">AI</a> ·
  <a href="https://github.com/Team-Hanmaum/hanmaum-docs">Docs</a>
</p>

---

**목차**

- [프로젝트 소개](#프로젝트-소개)
- [서비스 흐름](#서비스-흐름)
- [시스템 구성](#시스템-구성)
- [기술 스택](#기술-스택)
- [입력·출력 계약](#입력-출력-계약)
- [폴더 구조](#폴더-구조)
- [분석 파이프라인과 제안 생성](#분석-파이프라인과-제안-생성)
- [로컬 실행](#로컬-실행)
- [개발 워크플로우](#개발-워크플로우)
- [검증과 배포](#검증과-배포)

<a id="프로젝트-소개"></a>

## 🌿 프로젝트 소개

한마음은 가족이 함께 부모님의 돌봄을 이어갈 수 있도록, 흩어진 소식과 확인한 돌봄 정보를 한 공간에 모읍니다. 가족이 남긴 소식을 AI가 분석해 변경 사항을 제안하고, 소유자가 검토한 내용을 일정·할 일·질문 등에 반영합니다.

이 저장소는 Spring 백엔드의 내부 분석 요청을 받는 FastAPI 서비스입니다. 소식 원문과 기존 돌봄 항목을 비교해 일정·할 일·질문·관찰·안내에 대한 제안을 근거와 함께 반환합니다. 원본과 제안의 저장, 가족 권한 확인, 최종 반영은 BE가 담당합니다.

<a id="서비스-흐름"></a>

## 🧭 서비스 흐름

```text
로그인 → 공간 생성 또는 초대 참여 → 홈
  └─ 소식 작성 → BE에 원본 저장 → BE 워커가 AI 분석 요청
       └─ AI가 소식·후보·근거 비교 → BE가 제안 검증 및 저장
            ├─ 소유자: 최종 반영 또는 미적용
            └─ 구성원: 승인 요청 → 소유자 검토

최종 반영 → 홈·돌봄 목록·상세에서 변경된 정보 확인
```

FE는 BE에서 분석 상태와 제안을 조회합니다. AI의 응답은 가족이 검토할 제안이며, 소식 저장과 분석은 별도로 처리합니다.

<a id="시스템-구성"></a>

## 🏗️ 시스템 구성

![한마음 시스템 아키텍처](https://raw.githubusercontent.com/Team-Hanmaum/hanmaum-docs/main/assets/development/system-architecture.png)

| 저장소 | 역할 |
| --- | --- |
| [Frontend](https://github.com/Team-Hanmaum/hanmaum-frontend) | React 모바일 웹, 소식 작성과 제안 검토, Vercel 배포 |
| [Backend](https://github.com/Team-Hanmaum/hanmaum-backend) | 사용자 인증·공간 권한, 업무 데이터 저장, AI 분석 작업 실행과 제안 반영 |
| [AI](https://github.com/Team-Hanmaum/hanmaum-ai) | 내부 분석 API, 모델 호출, 제안과 근거 검증, Lightsail 배포 |

운영 배포에서 AI는 Lightsail 내부 네트워크에 BE 전용 HTTPS 주소를 제공합니다. AI는 자체 SQLite에 중복 호출 방지용 수신 기록을 보관하고, 모델 호출은 별도 외부 통신망을 통해 OpenAI로 보냅니다. 연결과 인증서 설정은 [운영 배포 안내](docs/deployment.md)와 [v2 BE 연동 확인](docs/v2-integration.md)을 참고하세요.

<a id="기술-스택"></a>

## 🛠️ 기술 스택

| 영역 | 기술 | 사용 목적 |
| --- | --- | --- |
| 실행·의존성 | Python 3.13.15 · uv | 프로젝트별 가상 환경과 `uv.lock` 기반 설치 |
| HTTP API | FastAPI · Uvicorn | 내부 분석 API, 상태 확인, 로컬 Swagger |
| 계약·설정 | Pydantic · pydantic-settings | 요청·응답 모델과 환경 변수 검증 |
| 모델 | HTTPX · OpenAI Responses API | 제한된 응답 스트림과 Structured Outputs 사용. 기본 설정은 GPT-6 Luna · low · 표준 처리 |
| 수신 기록 | SQLite · HMAC-SHA256 | 계약 버전·요청 ID별 중복 수신 판정 |
| 품질·배포 | Ruff · pytest · GitHub Actions · Docker Compose · AWS Lightsail | 코드·계약 검증 및 컨테이너 배포 |

<a id="입력-출력-계약"></a>

## 🔄 입력·출력 계약

| 구분 | v2 기준 |
| --- | --- |
| 요청 | `POST /v2/analyses`에 비압축 UTF-8 JSON 전송. `X-Internal-Api-Key`로 인증하며 `X-Request-Id`와 본문 `requestId`는 같은 UUID 사용 |
| 분석 자료 | 소식 원문 `content`, 작성 시각 `recordedAt`과 `Asia/Seoul`, BE가 전달한 후보의 `currentValues`와 `basis` |
| 성공 | `requestId`와 `suggestions`. 제안마다 유형·작업·대상·근거·검토 사유를 계약에 맞춰 반환 |
| 오류 | HTTP 상태와 `code`·`message`. 전송 바이트 초과, 필드 검증 실패, 중복 수신, 과부하, 제공자 오류를 구분 |
| 크기 | 요청 1 MiB·응답 512 KiB. 바이트 상한과 필드별 개수·UTF-16 길이 상한을 함께 적용 |

`evidence`는 이번 소식의 연속된 인용문이고, `basisReferences`는 비교에 사용한 기존 후보의 근거를 가리킵니다. UPDATE에서 생략한 필드는 기존 값을 유지하며, 원문이 삭제를 명시한 경우에만 `clearFields`로 삭제를 제안합니다. 질문 답변은 `changes.answerText`에 담습니다.

v2 기준은 `v2-20261021.1`이며, v1 경로와 모델은 별도로 유지합니다. 전체 필드와 오류 매핑은 [v2 계약](docs/ai-contract-v2.md), 기존 경로는 [v1 계약](docs/ai-contract.md)을 참고하세요.

<a id="폴더-구조"></a>

## 📁 폴더 구조

주요 폴더와 진입 파일을 기준으로 정리했습니다.

```text
hanmaum-ai/
├── .githooks/                  # Python·의존성 변경의 커밋 전 Ruff 검사
├── .github/                    # 이슈·PR 템플릿과 GitHub Actions
│   ├── ISSUE_TEMPLATE/         # 이슈 작성 양식
│   ├── workflows/              # AI CI · Lightsail 배포
│   └── pull_request_template.md
├── app/                        # FastAPI 앱, 내부 인증, 공통 오류와 v1 분석
│   ├── main.py                 # 앱 생성, 라우터와 제공자 연결
│   └── v2/                     # v2 HTTP, 계약 검증, 수신 기록과 제공자
│       ├── api.py              # 분석 요청과 준비 상태 연결
│       ├── schemas.py          # v2 입력·출력 모델
│       ├── services.py         # 수신 기록과 제공자 호출 조율
│       ├── prompt.py           # 모델 지시와 모델 입력 구성
│       └── structured_output.py  # 모델 출력 스키마와 변환
├── deploy/                     # Compose, 릴리스 선택·반영, 배포 검사
│   ├── aws/                    # GitHub OIDC 역할·권한 예시
│   └── systemd/                # 내부 TLS 인증서 갱신 서비스와 타이머
├── docs/                       # v1/v2 계약, 수신 기록, 연동·운영 배포 안내
├── evals/                      # 합성 회귀·독립 평가 사례와 채점 기준
├── scripts/                    # 공통 fixture 무결성 검사·생성, 모델 평가 실행
├── tests/                      # API·계약·수신 기록·평가·배포 자동 테스트
│   ├── support/                # 여러 테스트에서 사용하는 설정·요청·가짜 응답
│   └── fixtures/               # v1·v2 합성 입력과 기대값
│       └── v2/                 # BE와 공유하는 사례, 본문, revision·manifest
├── .env.example                # 로컬 설정 예시
├── .python-version             # uv가 사용하는 Python 버전
├── pyproject.toml              # 의존성·Ruff·pytest 설정
├── uv.lock                    # 잠긴 의존성 목록
├── Dockerfile                 # AI 실행 이미지 구성
└── AGENTS.md                  # 코드 작성·작업 워크플로우 지침
```

라우터는 HTTP 입출력, 서비스는 분석 흐름, 제공자 어댑터는 외부 호출을 담당합니다. [코드 구조와 책임](AGENTS.md#코드-구조와-책임)에서 파일별 역할을 확인할 수 있습니다.

<a id="분석-파이프라인과-제안-생성"></a>

## ⚙️ 분석 파이프라인과 제안 생성

1. 내부 키를 확인하고 본문 크기·JSON 구조·요청 ID를 검사합니다.
2. 정규화한 요청을 HMAC으로 비교해 기존 수신 기록을 판정합니다. 신규 요청은 제공자 슬롯을 확보한 뒤 기록을 원자적으로 등록합니다. 최초 등록에 성공한 요청만 모델을 호출합니다.
3. OpenAI 제공자가 시스템 프롬프트와 구조화된 출력 스키마를 사용합니다. 상대 날짜는 원본 `recordedAt`과 `Asia/Seoul`을 기준으로 해석합니다.
4. 모델 출력을 BE 형식으로 변환한 뒤, 요청 당시 후보·버전·근거를 기준으로 검증합니다. 계약을 위반한 제안이 하나라도 있으면 응답 전체를 오류 처리합니다.

| 작업 | 제안 내용 |
| --- | --- |
| `CREATE` | 원문에 근거한 새 돌봄 항목 추가 |
| `UPDATE` | 기존 항목에서 바뀐 값 또는 원문에 명시된 필드 삭제 |
| `RESOLVE` | 기존 질문에 대한 답변 |
| `LINK_EVIDENCE` | 값을 바꾸지 않고 관련 근거 연결 검토 |

같은 ID와 본문을 다시 보내면 `409 REQUEST_ALREADY_RECEIVED`, 같은 ID에 다른 유효 본문을 보내면 `409 REQUEST_ID_REUSED`를 반환합니다. 제공자 기본 대기 시간은 20초이며 지원하는 단일 앱·워커 구성에서 제공자 동시 슬롯은 2개입니다. 수신 기록과 타임아웃·오류 상세는 [수신 기록 안내](docs/v2-receipts.md)와 [v2 계약](docs/ai-contract-v2.md)에 정리했습니다.

<a id="로컬-실행"></a>

## 🚀 로컬 실행

**필수 환경: Python 3.13.15 · uv 0.12.19 이상**

[uv 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른 뒤, PowerShell에서 실행합니다. `uv python install`은 `.python-version`에 지정된 버전을 설치합니다.

```powershell
git clone --branch dev https://github.com/Team-Hanmaum/hanmaum-ai.git
Set-Location hanmaum-ai
uv python install
uv sync --locked --dev
if (!(Test-Path .env)) { Copy-Item .env.example .env }
git config core.hooksPath .githooks
uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

- [상태 확인](http://127.0.0.1:8000/health)과 [로컬 Swagger](http://127.0.0.1:8000/docs)를 열고, 종료할 때는 실행 터미널에서 `Ctrl+C`를 누릅니다.
- 새 `.env.example` 설정으로는 v1 stub이 켜져 있고 v2는 비활성 상태입니다. `GET /health/ready`는 v1 설정을 확인합니다.
- 로컬 단독 실행에는 Docker나 PostgreSQL이 필요하지 않습니다. 프로세스 환경 변수가 `.env`보다 우선하며 설정을 바꾼 뒤 서버를 재시작합니다.

<a id="v2-http-실행"></a>

### v2 HTTP 실행과 Swagger 확인

1. AI 전용 SQLite 수신 저장소를 최초 한 번 준비합니다. 기존 저장소를 사용 중이면 같은 파일과 키를 유지합니다.

   ```powershell
   $receiptDir = New-Item -ItemType Directory -Force -Path ../ai-data
   $receiptDb = Join-Path $receiptDir.FullName receipts.sqlite3
   uv run --locked python -m app.v2.initialize_receipts $receiptDb
   ```

2. `.env`에 `APP_ENV=local`, `AI_V2_PROVIDER=stub`, `AI_V2_RECEIPT_DB_PATH`를 설정합니다. SQLite 경로에는 위에서 초기화한 파일의 절대 경로를 입력합니다. `AI_V2_ACTIVE_HMAC_KEY_VERSION=local-1`을 지정하고 `AI_V2_HMAC_KEYS`에 `{"local-1":"<별도로 생성한 32바이트 이상의 무작위 키>"}` 형식으로 설정합니다. 내부 인증키와 HMAC 키는 별도로 관리합니다.
3. 서버를 재시작하고 Swagger의 **Authorize**에 `AI_INTERNAL_API_KEY`를 입력합니다. `GET /health/ready/v2`로 설정과 저장소 준비 여부를 확인합니다.
4. `POST /v2/analyses`에서 Swagger 예시나 [합성 요청](tests/fixtures/v2/payloads/requests/base.json)을 선택합니다. 본문과 헤더에 사용하지 않은 동일 UUID를 넣어 첫 요청의 `200`·`suggestions: []`와 재전송의 `409 REQUEST_ALREADY_RECEIVED`를 확인합니다.

실제 모델을 사용하려면 초기화된 저장소에 `AI_V2_PROVIDER=openai`, `OPENAI_API_KEY`와 별도 무작위 `AI_INTERNAL_API_KEY`를 설정합니다. Swagger·BE에는 내부 인증키를 사용합니다. 모델 설정 예시는 [.env.example](.env.example), 별도 포트 실행과 BE 연결 절차는 [v2 연동 안내](docs/v2-integration.md)를 참고하세요.

<a id="개발-워크플로우"></a>

## 🤝 개발 워크플로우

1. 이슈 생성 또는 기존 이슈 확인
2. 최신 `dev`에서 이슈 단위 작업 브랜치 생성
3. 구현·검증 후 사용자 확인을 거쳐 커밋
4. 푸시 후 `dev` 대상 PR 생성
5. 팀원 1명 이상의 승인과 필수 검사 통과
6. `dev`에 머지
7. 머지된 커밋의 AI CI 성공 후 Lightsail 자동 배포

| 항목 | 규칙 | 예시 |
| --- | --- | --- |
| 이슈 | `[Type] 작업 내용` · 담당자·라벨 지정 | `[Docs] AI 실행 안내 정리` |
| 브랜치 | `type/이슈번호-영문-작업명` | `docs/123-ai-readme` |
| 커밋 | `type: 핵심 변경 내용 (#이슈번호)` 한 줄 | `docs: AI 실행 안내 정리 (#123)` |
| PR | `Type(#이슈번호): 핵심 작업 내용` | `Docs(#123): AI 실행 안내 정리` |

커밋 메시지는 표의 형식으로 한 줄만 작성합니다. 작업 타입은 `feat`, `fix`, `chore`, `docs`, `refactor`, `style`을 사용합니다. `dev`·`main`에는 직접 커밋하거나 푸시하지 않습니다. 스택 PR의 base는 합의한 부모 브랜치를 사용합니다. PR은 [템플릿](.github/pull_request_template.md)에 이슈·변경 내용·검증 결과를 기재합니다. 담당자, Git 훅, 리뷰 기준은 [AGENTS.md](AGENTS.md)를 참고하세요.

<a id="검증과-배포"></a>

## ✅ 검증과 배포

AI 레포 루트에서 CI와 같은 기본 검사를 실행합니다.

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/fixture_manifest.py
uv run --locked pytest --junitxml=test-results/pytest.xml
```

[공통 fixture](tests/fixtures/v2/README.md) 검사는 BE와 공유하는 사례·기대값·파일 해시와 생성 본문을 확인합니다. pytest는 가짜 제공자와 격리된 저장소로 API·계약·중복 차단·타임아웃을 검증합니다. `evals/`의 합성 데이터 평가는 계약 검사와 별도로 제안의 의미 정확도·근거·지연·토큰 사용량을 측정합니다. 유료 OpenAI 호출이 필요한 명령과 평가 기준은 [모델 품질 재평가 절차](docs/v2-integration.md#6-합성-데이터로-모델-품질-재평가)에 있습니다.

![한마음 CI/CD 파이프라인](https://raw.githubusercontent.com/Team-Hanmaum/hanmaum-docs/main/assets/development/ci-cd.png)

| 시점 | 실행 내용 |
| --- | --- |
| PR 생성·갱신·재오픈, `dev`·`main` push | **AI CI**에서 Ruff → fixture → pytest → 배포 구성·격리 TLS 컨테이너 검증. JUnit 결과는 아티팩트로 업로드 |
| `dev` push의 AI CI 성공 | **Deploy AI**가 현재 `dev`와 일치하는 커밋을 재검증하고, GHCR 이미지 digest로 Lightsail `production`에 배포 |
| 수동 배포 | Actions의 **Deploy AI → Run workflow → dev**에서 실행. 최초 초기화 옵션은 수동 배포에서 사용 |

배포 구성을 로컬에서 확인하려면 Docker가 실행 중인 환경에서 아래 명령을 실행합니다. 셸 스크립트 구문 검사까지 포함하려면 Git Bash를 사용합니다.

```bash
for script in deploy/*.sh; do bash -n "$script"; done
uv run --locked python deploy/validate.py
docker build --tag hanmaum-ai:ci .
uv run --locked python -m deploy.smoke hanmaum-ai:ci
```

격리 smoke test는 실제 모델 호출 없이 TLS·내부 인증·분석 준비 상태·수신 기록 영속성을 확인합니다. 워크플로우는 [AI CI](.github/workflows/ci.yml)와 [Deploy AI](.github/workflows/deploy.yml), 운영 설정과 최초 배포·복구는 [운영 배포 안내](docs/deployment.md)를 참고하세요.
