#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID -eq 0 && "$(uname -m)" == x86_64 ]]
# The BE deployment uses this exact lock. GitHub concurrency is repository-scoped.
exec 9>/run/lock/hanmaum-deploy.lock
flock -w 600 9 || { echo 'Another deployment is still active.' >&2; exit 1; }
cd "$(dirname "$0")/.."
python3 -m deploy.host "$1"
