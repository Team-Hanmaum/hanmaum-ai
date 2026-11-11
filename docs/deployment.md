# Lightsail AI 운영 배포

기존 Sydney Lightsail `hanmaum-server-01`에 별도 Compose 프로젝트 `hanmaum-ai-prod`로 배포한다.
기준 브랜치는 `dev`다. dev push의 **AI CI**가 성공하면 **Deploy AI**가 자동 실행된다.
Actions의 **Deploy AI → Run workflow → dev** 수동 실행과 최초 초기화 옵션도 유지한다.
CD는 배포할 커밋을 먼저 선택하고, build job이 그 커밋을 다시 검증하여 통과한 이미지 digest만 deploy에 전달한다.
과거 실행 재실행은 현재 dev와 다르면 배포하지 않는다.
이 문서는 절차이며 AWS 설정·실제 배포·모델 품질·BE 통합 검증의 완료 기록이 아니다.

## 자동 배포 조건과 실행 흐름

1. `dev`에 PR을 머지하면 해당 push의 `AI CI`가 실행된다.
2. `workflow_run.completed`로 CD가 시작되면, 읽기 권한만 가진 prepare job이 이벤트를 검증한다.
   같은 저장소의 `AI CI`·`push`·`dev`·`completed`·`success`인 경우에만 후보 커밋을 선택한다.
   PR(같은 저장소의 PR 포함), 다른 브랜치·저장소, 실패·취소·건너뛴 CI는 배포 대상으로 선택하지 않는다.
3. CI의 `head_sha`가 현재 원격 dev와 같을 때만 build/deploy로 전달한다. 자동 실행의
   `GITHUB_SHA`는 CD 기본 브랜치 커밋일 수 있으므로 배포 대상 대신 사용하지 않는다.
   checkout·이미지 태그/라벨·아카이브·release.json·서버 적용에는 선택된 `RELEASE_SHA`를 사용한다.
4. 기존 Ruff·pytest·fixture·배포 설정·격리 TLS 컨테이너 검증 후 GHCR에 이미지를 게시한다.
5. production 환경 규칙과 배포 대기열을 거쳐 Lightsail에 반영하고 TLS/readiness를 확인한다.
   최초 초기화는 자동 실행에서 항상 비활성화한다. 초기 DB·CA가 없으면 자동 생성하지 않고 중단한다.

부적격 이벤트에는 선택 SHA를 출력하지 않아 이미지 쓰기 권한과 배포 대기열에 진입하지 않는다.
prepare 시점에 이미 오래된 실행은 정상 건너뛰며, GitHub 조회 실패는 배포 선택 실패로 처리한다.
선택 후 dev가 바뀌는 경우도 build 시작·배포 대기 후·서버 잠금 획득 후·활성화 직전에 다시 검사한다.
뒤늦은 과거 실행은 중단하며 실행 중인 배포를 강제 취소하지 않는다.
같은 최신 dev의 수동 재실행 또는 CI 재실행은 다시 배포할 수 있으며, 수신 기록은 보존한다.

수동 실행은 CI 완료 이벤트 없이도 선택한 최신 dev를 동일한 build 검증에 통과시킨 후 배포한다.
`initialize_ai`는 `workflow_dispatch`에서만 적용하며, 일반 재배포에서는 선택하지 않는다.
production 환경의 승인 규칙이 있다면 자동 실행도 승인을 기다린다. 이 변경으로 환경 보호 규칙을 해제하지 않는다.
정상 배포 확인은 모델 API를 호출하지 않는다. 프롬프트·모델 변경의 실제 품질 평가는 PR에서 별도로 확인한다.

자동 시작은 이 workflow가 기본 브랜치인 dev에 반영된 뒤부터 적용된다.
로컬 이벤트 테스트만으로 GitHub 이벤트 연결·AWS 자동 배포가 완료됐다고 판단하지 않는다.
최초 머지 후 같은 SHA의 `AI CI` 성공 → `Deploy AI` 실행 → build/deploy 성공과 서버 release SHA를 확인한다.
이벤트의 SHA·권한 기준은 [GitHub workflow_run 문서](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)를 따른다.

## 구성

- AI 단일 Uvicorn 프로세스가 `8443`에서 TLS를 직접 종료한다. 평문 프록시 구간은 없다.
- `hanmaum-ai-link`는 external/internal bridge다. 후속 BE Compose에서 BE만 추가한다.
  주소는 `https://hanmaum-ai:8443`이며 가비아 DNS 설정은 필요 없다.
- AI 전용 `egress` 네트워크로 모델 제공자 HTTPS를 호출한다. AI 호스트 포트는 공개하지 않는다.
- AI CD는 BE의 Nginx·PostgreSQL·Compose를 수정하지 않는다.
- 호스트에서 CA와 SAN `hanmaum-ai` 서버 인증서를 생성한다. BE에 CA 공개 인증서를 신뢰시켜야 한다.
  인증서 검증 해제, AI 포트 공개, CA/서버 개인키의 BE 전달은 하지 않는다.
- v1은 disabled, v2는 openai이며 운영 stub은 금지한다.
- `AI_MEMORY_LIMIT`을 명시한다. 최초 시험값 제안은 `512m`이며 실제 분석 중 측정 후 확정한다.
  이 구성은 256~1024m만 허용하나 안정성을 보장하는 범위는 아니다. ASGI 워커는 1개다.

현재 BE 운영 파일은 워커 비활성화·`https://ai.invalid`·v1 상태다. AI 배포만으로 연결되지 않는다.

## 1. AWS IAM

리소스 계정 **Hanmaum / 642122109209**에서 작업한다.

1. IAM → 정책 → 정책 생성 → JSON에 [permissions.json](../deploy/aws/permissions.json) 입력.
   이름 예: `HanmaumGitHubAIDeployPolicy`.
2. IAM → 역할 → 역할 생성 → 사용자 지정 신뢰 정책에 [trust.json](../deploy/aws/trust.json) 입력.
3. 위 정책을 연결하고 역할 이름을 `HanmaumGitHubAIDeploy`로 지정한다.
4. ARN `arn:aws:iam::642122109209:role/HanmaumGitHubAIDeploy`를 GitHub Variable에 사용한다.

기존 OIDC 공급자를 재사용한다. 신뢰 정책은 AI 저장소 immutable ID와 production environment를
제한한다. IAM 사용자·장기 AWS Access Key는 필요 없다. 정책은 인스턴스 SSH 규칙 조회·추가·제거이며
BE의 S3 권한은 포함하지 않는다. 조회 API의 `Resource: "*"`와 달리 변경은 인스턴스 ARN으로 제한한다.

## 2. SSH와 호스트

Ubuntu amd64, Docker, Compose **2.30 이상**(v5 포함), Python 3, OpenSSL, flock, systemd가 필요하다.
기존 BE 서버의 설치를 활용한다. 본인 PC에서 AI 배포 전용 키를 새 경로에 생성한다.

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE/.ssh/hanmaum-ai-deploy" -C "hanmaum-ai-actions"
```

Actions 비대화형용 키의 passphrase는 비워 둔다. 기존 키 파일에 덮어쓰지 않는다.
Lightsail 브라우저 SSH에서 ubuntu의 `~/.ssh/authorized_keys`에 공개키 .pub 내용 앞에
**`restrict `를 붙여 한 줄로 추가**한다. 기존 BE 배포 키와 같은 제한이다.

```text
restrict ssh-ed25519 실제_공개키_내용 hanmaum-ai-actions
```

`restrict`는 포트·에이전트·X11 포워딩, PTY 할당, 사용자 SSH rc 실행을 막는다.
현재 CD의 비대화형 명령·SCP 전송에는 필요한 기능이 아니다. 실행 명령 자체를 제한하는 옵션은 아니다.
이미 같은 AI 공개키를 제한 없이 추가했다면 해당 줄 앞에 붙인다. 제한 없는 중복 줄을 남기지 않는다.
이 접두사는 서버의 authorized_keys에만 사용하며 개인키 Secret·known_hosts에는 붙이지 않는다.
기존 키를 지우지 않으며 .ssh는 700, authorized_keys는 600으로 유지한다.
기존 BE처럼 ubuntu의 `sudo -n`을 사용하므로 SSH 키는 호스트 전체 관리 권한이다.
IAM 역할 분리가 OS 수준의 BE/AI 권한 격리를 의미하지는 않는다.

브라우저 SSH에서 서버 호스트 공개키를 확인한다.

```bash
sudo cat /etc/ssh/ssh_host_ed25519_key.pub
```

`DEPLOY_SSH_KNOWN_HOSTS`에는 `16.178.232.151 ssh-ed25519 <확인한 서버 공개키>` 한 줄을 등록한다.
매 배포에서 ssh-keyscan 결과만 새로 신뢰하지 않는다.
22번 인바운드는 기존 관리자 IP/32·브라우저 SSH를 유지한다.
CD가 runner 공인 IPv4/32만 일시 추가하고 자신이 추가한 규칙만 제거한다.

## 3. GitHub 환경

AI 저장소 → Settings → Environments → New environment → `production`.
**Deployment branches and tags → Selected branches and tags → dev만 허용**한 뒤 값을 등록한다.
dev 보호·PR 승인 규칙을 유지한다. BE 저장소의 같은 이름 environment는 자동 공유되지 않는다.

| Secrets | 값 |
| --- | --- |
| `DEPLOY_SSH_PRIVATE_KEY` | AI 배포 개인키 전체, 헤더·푸터 포함 |
| `DEPLOY_SSH_KNOWN_HOSTS` | 검증한 서버 호스트 공개키 한 줄 |
| `AI_INTERNAL_API_KEY` | **이미 배포된 BE와 동일한 기존 키** |
| `OPENAI_API_KEY` | AI 전용 모델 API 키 |
| `AI_V2_HMAC_KEYS` | 별도 난수 키 JSON, 예: `{"prod-1":"<32바이트 이상 별도 난수>"}` |

내부키를 잊었다면 기존 비밀 저장 위치나 서버의 BE 기준 파일에서 본인이 확인한다.
새 키로 바꾸고 BE 재배포하는 방식은 키 보존 검사에 차단된다.
AI 배포는 서버 BE 기준 키와 AI 후보 키를 출력 없이 비교한다.
HMAC 키는 본인 PC에서 한 번 생성하고 안전하게 보관한다.

```powershell
uv run --locked python -c "import secrets; print(secrets.token_urlsafe(48))"
```

키를 코드·채팅·이슈·스크린샷에 올리지 않는다. 내부키·모델키·HMAC 키는 서로 다르게 사용한다.

| Variables | 값 |
| --- | --- |
| `AWS_DEPLOY_ROLE_ARN` | 위 AI 역할 ARN |
| `DEPLOY_HOST` | `16.178.232.151` |
| `AI_MEMORY_LIMIT` | 시험값 제안 `512m`, 부하 측정 후 조정 |
| `AI_V2_ACTIVE_HMAC_KEY_VERSION` | JSON 키 버전과 같은 `prod-1` |
| `AI_V2_MODEL` | 선택, 미설정 시 기존 기본값 `gpt-6-luna` |
| `AI_V2_REASONING_EFFORT` | 선택, 미설정 시 `low` |
| `AI_V2_MAX_OUTPUT_TOKENS` | 선택, 미설정 시 `4096` |

region·인스턴스는 workflow에 고정한다. 이미지 digest는 build 결과에서 자동 전달한다.
타임아웃·service tier는 기존 앱 기본값을 유지한다.
Secrets 저장만으로 컨테이너 값은 바뀌지 않으며 다음 성공한 배포에서 반영된다.

## 4. 최초 배포

1. PR 검토·필수 CI 후 dev에 머지한다. workflow가 기본 브랜치에 있어야 수동 버튼이 나타난다.
   초기 설정이 없는 서버에는 이 시점의 자동 배포가 실패할 수 있으며, 자동 초기화로 우회하지 않는다.
2. BE 배포가 없는 시점에 **Deploy AI → Run workflow → dev** 선택.
3. 최초 한 번만 `initialize_ai` 체크하여 CA·인증서·새 수신 기록 저장소를 만든다.
   이후 배포는 체크하지 않는다. 초기화된 DB가 유실되면 체크해도 재생성하지 않는다.
4. build → deploy 각 step을 확인한다. 성공 시 내부 TLS·인증된 v2 readiness가 통과한 것이다.
5. 서버 상태 확인:

```bash
sudo docker ps --filter label=com.docker.compose.project=hanmaum-ai-prod
sudo docker stats --no-stream
sudo cat /opt/hanmaum-ai/deployment-state.json
sudo systemctl status hanmaum-ai-tls-renew.timer --no-pager
```

readiness는 설정·SQLite 접근·스키마만 검사하며 모델은 호출하지 않는다.
모델 API 키의 실제 유효성·잔액·모델 접근권한·의미 품질 확인은 별도 합성 요청으로 수행한다.
유료 호출은 범위·비용을 합의한 뒤 진행한다.

## 5. 후속 BE 연동

이번 AI 배포 파일은 BE를 변경하지 않는다. 후속 BE 변경 시 다음을 함께 적용한다.

1. BE 서비스에 external network `hanmaum-ai-link` 연결.
2. `/opt/hanmaum-ai/tls/ca.crt` 공개 인증서를 BE TLS 신뢰 설정에 **추가**.
   기존 Java 공인 CA 목록을 대체하지 않아 OAuth 등 외부 HTTPS도 유지해야 한다.
3. `AI_BASE_URL=https://hanmaum-ai:8443` 설정, 내부키 유지.
4. 새 분석의 계약을 v2로 설정하고 워커는 검증 전까지 비활성화.
5. 기존 v1/QUEUED 실행 확인. 전역 설정만으로 접수된 실행의 버전이 변하지 않는다.
6. 인증·오류·제안 저장·조회 검증 후 워커 활성화.

임시 docker network connect만 사용하면 BE 재배포 때 사라질 수 있다. BE 운영 Compose에 반영한다.
OpenAI/HMAC 키·SQLite를 BE에 전달하지 않는다.
BE 워커 동시성과 AI의 기존 제공자 슬롯 2개·진입 슬롯 4개를 맞춰 부하를 확인한다.

## 운영 상태와 복구

| 호스트 경로 | 내용 |
| --- | --- |
| `/opt/hanmaum-ai/releases/<sha>-<run>-<attempt>` | 커밋된 배포 파일, root 전용 runtime.env·metadata |
| `/opt/hanmaum-ai/current` | 마지막 정상 AI release |
| `/opt/hanmaum-ai/deployment-state.json` | 적용 중·성공·실패·복구 필요 상태 |
| `/opt/hanmaum-ai/data/receipts.sqlite3` | 영속 수신 기록 |
| `/opt/hanmaum-ai/credential-baseline.env` | 최초 내부키·HMAC 기준 |
| `/opt/hanmaum-ai/ca` | root 전용 CA 개인키, 컨테이너에 마운트하지 않음 |
| `/opt/hanmaum-ai/tls` | AI UID 10001이 읽는 서버 인증서·개인키·CA 공개 인증서 |
| `/opt/hanmaum-ai/backups` | 배포 전 SQLite online backup |

GitHub concurrency는 저장소 간 공유되지 않는다. AI 활성화·인증서 갱신은 BE와 같은
`/run/lock/hanmaum-deploy.lock`을 사용한다. AI는 최대 10분 대기한다.
현재 BE는 잠금 실패 시 배포를 중단하므로 겹친 BE 실행은 이후 새로 실행해야 한다.

후보 health 실패 시 기존 이미지·설정으로 복구하고 CD는 실패로 표시한다. 최초 실패는 후보를 정지한다.
**DB는 과거 백업으로 자동 되돌리지 않는다.** 새 후보가 접수한 기록을 버리면 중복 호출 방지가 깨진다.
강제 종료로 applying이 남거나 복구 실패 시 후속 배포를 차단한다.
실제 컨테이너와 마지막 정상 release를 확인하고 그 Compose를 up --wait로 복원한 뒤
current 링크·상태 파일을 정상 상태와 일치시켜야 한다. 상태 파일만 지워 재배포하지 않는다.

내부키·HMAC 매핑·활성 키 버전은 일반 재배포로 바꿀 수 없다. 키 회전은 기존 수신 기록의 키 버전,
BE 동기화·이전 이미지 복구 가능성을 검토한 별도 절차다.
모델 API 키는 보존 검사 대상이 아니며 Secret 변경 후 재배포로 교체할 수 있다.

서버 인증서는 365일 유효하며 systemd가 매일 확인해 잔여 30일 미만에 동일 CA로 갱신한다.
AI만 재생성하므로 짧은 분석 중단이 있을 수 있다. CA는 자동 교체하지 않는다.
잠금 충돌로 건너뛴 검사는 다음 날 재실행된다. 타이머 실패를 확인한다.

```bash
sudo journalctl -u hanmaum-ai-tls-renew.service --since '7 days ago' --no-pager
```

SQLite와 키는 재배포와 별도로 보존한다. 자동 TTL·삭제·호스트 밖 백업은 아직 없다.
**로컬 백업은 Lightsail 전체 디스크 유실을 보호하지 못한다.** 실제 분석 사용 전 수신 기록·키의
서버 밖 백업·복구 방식을 정해야 한다. 오래된 SQLite만 복원하면 이후 ID는 다시 호출될 수 있다.
유실 시 빈 DB로 재개하지 않는다. BE의 PostgreSQL S3 백업은 AI SQLite를 포함하지 않는다.
디스크를 관찰하며 용량 확보를 이유로 기록·HMAC 키를 삭제하지 않는다.
release·이미지·로컬 백업은 자동 삭제하지 않으며 현재 실행·복구 대상을 확인한 후 정리한다.

## 검증

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python deploy/validate.py
docker build --tag hanmaum-ai:deploy-check .
uv run --locked python -m deploy.smoke hanmaum-ai:deploy-check
```

smoke는 고유 테스트 네트워크·합성 키·임시 DB만 사용한다. 테스트 네트워크는 모두 internal로
실제 모델로 나갈 수 없다. 인증서 체인·호스트명, 인증 없는 요청 거절, 운영 문서 비공개,
실제 환경변수의 $ 보존, 컨테이너 재생성 후 기록 보존을 검사한다. 기존 로컬 AI 서버는 건드리지 않는다.

참고: [Docker networks](https://docs.docker.com/reference/compose-file/networks/),
[Compose raw env_file](https://docs.docker.com/reference/compose-file/services/#env_file),
[Uvicorn TLS](https://uvicorn.dev/settings/#https),
[GitHub AWS OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).
현재 actionlint 1.7.12는 GitHub가 지원하는 concurrency.queue를 인식하지 않는다.
해당 진단을 일반 문법 오류와 구분하며 전체 도구 검사를 통과했다고 표시하지 않는다.
