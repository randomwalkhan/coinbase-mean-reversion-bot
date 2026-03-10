#!/bin/zsh
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-mac@100.86.132.84}"
REMOTE_DIR="${REMOTE_DIR:-/Users/mac/services/coinbase-mean-reversion-bot}"
ENV_NAME="${ENV_NAME:-quant}"
SLEEP_SECONDS="${SLEEP_SECONDS:-900}"
PLIST_NAME="${PLIST_NAME:-com.randomwalkhan.coinbase-bot}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SSH_OPTS=(
  -o
  StrictHostKeyChecking=accept-new
  -o
  BatchMode=yes
)

if [[ -n "${SSH_IDENTITY_FILE:-}" ]]; then
  SSH_OPTS+=(-o IdentitiesOnly=yes -i "${SSH_IDENTITY_FILE}")
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Missing ${REPO_DIR}/.env" >&2
  exit 1
fi

ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" "mkdir -p '${REMOTE_DIR}'"

rsync -az \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.migration_backup/' \
  "${REPO_DIR}/" "${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" \
  "ENV_NAME='${ENV_NAME}' SLEEP_SECONDS='${SLEEP_SECONDS}' PLIST_NAME='${PLIST_NAME}' zsh '${REMOTE_DIR}/scripts/remote_bootstrap_mac_mini.sh' '${REMOTE_DIR}'"

echo
echo "Deployment finished."
echo "Remote host: ${REMOTE_HOST}"
echo "Remote dir : ${REMOTE_DIR}"
