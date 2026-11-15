# Lightsail AI 운영 배포

AI는 Sydney Lightsail의 `hanmaum-server-01`에서 독립 Compose 프로젝트 `hanmaum-ai-prod`로 실행한다. LB나 공개 AI 포트 없이 Uvicorn이 `8443`에서 TLS를 직접 종료한다. BE는 내부 네트워크 `hanmaum-ai-link`를 통해 `https://hanmaum-ai:8443`을 호출하고, AI는 전용 egress 네트워크로 OpenAI에 접속한다. 운영은 v1 비활성화, v2 OpenAI, 단일 ASGI 워커 구성이다.

## 자동·수동 배포

1. `dev` push의 [AI CI](../.github/workflows/ci.yml)가 성공하면 [Deploy AI](../.github/workflows/deploy.yml)가 시작된다. 같은 저장소의 성공한 `dev` push만 선택한다.
2. CI의 `head_sha`가 현재 `dev`와 일치하면 해당 커밋을 재검증하고 GHCR에 게시한다. 빌드·전송·서버 적용은 같은 `RELEASE_SHA`와 이미지 digest를 사용한다. 배포 파일은 선택한 커밋의 `deploy/`에서 가져온다. 최신 `dev` 여부는 배포 대기 이후와 서버 활성화 전에도 확인한다.
3. `production` 환경 규칙과 배포 잠금을 거쳐 Lightsail에 반영한다. 같은 SHA의 CI와 Deploy AI 결과, 서버의 적용 SHA, TLS/readiness를 확인한다.

수동 실행은 **Deploy AI → Run workflow → dev**를 사용한다. 검증 절차는 자동 배포와 같다. `initialize_ai`는 새 서버를 처음 준비할 때만 수동으로 선택한다. 기존 수신 DB가 유실된 상태에서는 초기화로 빈 파일을 만들지 않는다.

## AWS IAM과 호스트

- GitHub OIDC용 IAM 역할 `HanmaumGitHubAIDeploy`에 [권한 정책](../deploy/aws/permissions.json)과 [신뢰 정책](../deploy/aws/trust.json)을 연결한다. 저장소와 `production`으로 신뢰 범위를 제한하고, 인스턴스 SSH 규칙만 수정한다.
- 호스트는 Ubuntu amd64, Docker, Compose 2.30 이상, Python 3, OpenSSL, flock, systemd를 사용한다.
- 전용 SSH 키는 새 경로에 만든다. 비대화형 Actions 전용 키이므로 passphrase는 비워 둔다.

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE/.ssh/hanmaum-ai-deploy" -C "hanmaum-ai-actions"
```

서버의 `ubuntu` 계정 `authorized_keys`에 `restrict `를 붙인 공개키 한 줄을 추가한다. 같은 키의 제한 없는 중복 줄은 제거한다. `.ssh`는 700, `authorized_keys`는 600으로 유지한다. 배포는 `sudo -n`을 사용하므로 이 키에 호스트 관리 권한이 있다.

신뢰하는 기존 SSH 경로에서 `sudo cat /etc/ssh/ssh_host_ed25519_key.pub`로 서버 키를 확인한다. `DEPLOY_SSH_KNOWN_HOSTS`에는 `<DEPLOY_HOST> ssh-ed25519 <검증한 호스트 공개키>`를 등록한다. CD는 실행 러너의 IPv4/32에만 임시 SSH 규칙을 추가하고 자신이 만든 규칙을 종료 시 제거한다.

## GitHub production 환경

AI 저장소의 `production` 환경에서 배포 브랜치를 `dev`로 제한한다. 아래 값을 등록하면 워크플로우가 실제 `runtime.env`를 생성한다. 컨테이너에는 다음 성공 배포부터 반영된다.

| Secret | 용도 |
| --- | --- |
| `DEPLOY_SSH_PRIVATE_KEY` | 전용 SSH 개인키 전체 |
| `DEPLOY_SSH_KNOWN_HOSTS` | 검증한 서버 호스트 공개키 한 줄 |
| `AI_INTERNAL_API_KEY` | 기존 BE와 같은 내부 인증키 |
| `OPENAI_API_KEY` | AI 전용 모델 API 키 |
| `AI_V2_HMAC_KEYS` | 버전별 별도 무작위 키 JSON. 키마다 최소 32바이트 |

| Variable | 용도 |
| --- | --- |
| `AWS_DEPLOY_ROLE_ARN` | AI용 IAM 역할 ARN |
| `DEPLOY_HOST` | Lightsail의 SSH 주소 |
| `AI_MEMORY_LIMIT` | 컨테이너 메모리 한도. 허용 범위 256~1024m, 부하 측정에 따라 설정 |
| `AI_V2_ACTIVE_HMAC_KEY_VERSION` | 위 HMAC JSON에 존재하는 활성 버전 |
| `AI_V2_MODEL` | 선택. 기본 `gpt-6-luna` |
| `AI_V2_REASONING_EFFORT` | 선택. 기본 `low` |
| `AI_V2_MAX_OUTPUT_TOKENS` | 선택. 기본 `4096` |

내부 인증키·모델 키·HMAC 키는 별도 값으로 관리한다. 내부 인증키와 HMAC 설정은 일반 재배포에서 기존 값과 비교한다. 교체는 BE 동기화·기존 기록 비교·이전 릴리스 복구를 검토한 별도 절차로 진행한다. 모델 키는 Secret 변경 후 재배포로 교체할 수 있다.

## BE 연동과 상태 확인

BE 운영 Compose에는 `hanmaum-ai-link`, 내부 HTTPS 주소와 신규 분석용 `v2`가 설정돼 있다. BE는 `/opt/hanmaum-ai/tls/ca.crt` 공개 CA를 기존 Java 신뢰 목록에 추가하고 서버 이름을 검증한다. AI 개인키·수신 DB는 AI 호스트에서 관리한다. 워커 활성화는 BE의 `ANALYSIS_WORKER_ENABLED`와 실제 배포 상태를 확인한다. 작업이 접수될 때 고정한 계약 버전은 설정 변경 후에도 유지한다.

```bash
sudo docker ps --filter label=com.docker.compose.project=hanmaum-ai-prod
sudo docker stats --no-stream
sudo cat /opt/hanmaum-ai/deployment-state.json
sudo systemctl status hanmaum-ai-tls-renew.timer --no-pager
```

readiness는 내부 인증·설정·SQLite 접근을 확인한다. 모델 키 권한·품질과 BE 제안 저장은 [v2 연동 안내](v2-integration.md), 운영 BE 설정은 [BE 배포 문서](https://github.com/Team-Hanmaum/hanmaum-backend/blob/dev/docs/deployment.md)를 참고한다.

## 복구와 보존

| `/opt/hanmaum-ai` 아래 경로 | 역할 |
| --- | --- |
| `releases/<sha>-<run>-<attempt>` · `current` | 릴리스 설정과 마지막 정상 배포 링크 |
| `deployment-state.json` | 적용·성공·실패·복구 상태 |
| `data/receipts.sqlite3` · `backups` | 영속 기록과 배포 전 SQLite 백업 |
| `credential-baseline.env` | 내부 인증키·HMAC의 최초 기준 |
| `ca` · `tls` | root 전용 CA 키와 서버 인증서. CA 개인키는 컨테이너에 마운트하지 않는다. |

- AI 배포와 인증서 갱신은 BE와 같은 `/run/lock/hanmaum-deploy.lock`을 쓴다. AI 배포는 최대 10분 대기하고, BE 배포는 잠금 충돌 시 중단된다.
- readiness 실패 시 이전 이미지·설정으로 돌아가고 최초 배포 실패 시 후보를 정지한다. SQLite는 새로 등록된 요청을 보존하기 위해 자동으로 되돌리지 않는다.
- 상태가 `applying`에 머물거나 롤백이 실패하면 실제 컨테이너와 마지막 정상 릴리스를 대조한다. 정상 Compose를 `up --wait`로 복원한 뒤 `current`와 상태 파일을 실제 상태와 일치시킨다.
- 인증서 유효 기간은 365일이다. systemd가 매일 확인하여 잔여 30일 미만이면 같은 CA로 갱신한다. 로그는 `sudo journalctl -u hanmaum-ai-tls-renew.service --since '7 days ago' --no-pager`로 확인한다.
- 자동 TTL·삭제·호스트 외부 백업은 구성돼 있지 않다. BE PostgreSQL 백업에는 AI SQLite가 포함되지 않는다. 수신 DB를 잃으면 빈 파일로 재개하지 않는다. 오래된 백업 복원은 그 이후의 요청 ID를 다시 호출할 수 있으므로 수신 기록과 HMAC 키를 함께 복구해야 한다.

## 검증

기본 검사는 [README](../README.md#검증과-배포)의 명령을 사용한다. 배포 구성과 TLS는 Docker를 켠 뒤 확인한다.

```sh
uv run --locked python deploy/validate.py
docker build --tag hanmaum-ai:deploy-check .
uv run --locked python -m deploy.smoke hanmaum-ai:deploy-check
```

Smoke test는 격리 네트워크와 합성 키·임시 DB로 TLS 인증서, 내부 인증, 운영 문서 비공개, 환경 값의 원문 보존, 수신 기록 영속성을 확인한다.
