#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${RUNNER_TEMP:?}" "${DEPLOY_HOST:?}" "${GH_TOKEN:?}"
: "${DEPLOY_SSH_PRIVATE_KEY:?}" "${DEPLOY_SSH_KNOWN_HOSTS:?}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" == "$RELEASE_SHA" ]]
python3 -c 'import ipaddress,os; assert ipaddress.ip_address(os.environ["DEPLOY_HOST"]).version == 4'
work="$(mktemp -d "$RUNNER_TEMP/hanmaum-ai-upload.XXXXXXXXXX")"
remote=""
options=(-4 -i "$work/key" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$work/known_hosts"
  -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
target="ubuntu@$DEPLOY_HOST"
cleanup() {
  local result=$?
  trap - EXIT
  if [[ "$remote" =~ ^/tmp/hanmaum-ai-cd\.[a-zA-Z0-9]{10}$ ]]; then
    ssh "${options[@]}" "$target" "rm -rf -- '$remote'" ||
      echo "::warning::Remove the private server upload directory $remote"
  fi
  rm -rf -- "$work"
  exit "$result"
}
trap cleanup EXIT
printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" > "$work/key"
printf '%s\n' "$DEPLOY_SSH_KNOWN_HOSTS" > "$work/known_hosts"
ssh-keygen -y -P '' -f "$work/key" > /dev/null
ssh-keygen -F "$DEPLOY_HOST" -f "$work/known_hosts" > /dev/null
mkdir "$work/payload"
# No untracked files, developer .env, or repository credentials are uploaded.
git archive "$RELEASE_SHA" deploy | tar -x -C "$work/payload"
python3 deploy/release.py write "$work/payload"
printf '%s' "$GH_TOKEN" > "$work/payload/registry-token"
for attempt in {1..12}; do
  if remote="$(ssh "${options[@]}" "$target" 'umask 077; mktemp -d /tmp/hanmaum-ai-cd.XXXXXXXXXX')"; then
    break
  fi
  sleep 5
done
[[ "$remote" =~ ^/tmp/hanmaum-ai-cd\.[a-zA-Z0-9]{10}$ ]]
tar -C "$work/payload" -czf "$work/release.tgz" .
scp "${options[@]}" "$work/release.tgz" "$target:$remote/release.tgz"
ssh "${options[@]}" "$target" \
  "umask 077; tar -xzf '$remote/release.tgz' -C '$remote'; sudo -n bash '$remote/deploy/activate.sh' '$remote'"
