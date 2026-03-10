#!/bin/zsh
set -euo pipefail

REPO_DIR="${1:-$HOME/services/coinbase-mean-reversion-bot}"
ENV_NAME="${ENV_NAME:-quant}"
PLIST_NAME="${PLIST_NAME:-com.randomwalkhan.coinbase-bot}"
STATUS_PLIST_NAME="${STATUS_PLIST_NAME:-com.randomwalkhan.coinbase-bot-status}"
SLEEP_SECONDS="${SLEEP_SECONDS:-900}"

find_conda_sh() {
  local candidates=(
    "$HOME/anaconda3/etc/profile.d/conda.sh"
    "$HOME/miniconda3/etc/profile.d/conda.sh"
    "$HOME/mambaforge/etc/profile.d/conda.sh"
    "$HOME/miniforge3/etc/profile.d/conda.sh"
    "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

CONDA_SH="$(find_conda_sh)"
if [[ -z "${CONDA_SH:-}" ]]; then
  echo "Could not find conda.sh on the remote Mac mini." >&2
  exit 1
fi

source "$CONDA_SH"
conda activate "$ENV_NAME"

cd "$REPO_DIR"
mkdir -p logs state "$HOME/Library/LaunchAgents"

python -m pip install -r requirements.txt

PYTHON_BIN="$(command -v python)"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
STATUS_PLIST_PATH="$HOME/Library/LaunchAgents/${STATUS_PLIST_NAME}.plist"

IMESSAGE_TARGET="$("${PYTHON_BIN}" - <<'PY'
from dotenv import dotenv_values
cfg = dotenv_values('.env')
print(cfg.get('IMESSAGE_TARGET', ''))
PY
)"

STATUS_REPORT_INTERVAL_SECONDS="$("${PYTHON_BIN}" - <<'PY'
from dotenv import dotenv_values
cfg = dotenv_values('.env')
print(cfg.get('STATUS_REPORT_INTERVAL_SECONDS', '1800'))
PY
)"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
      <string>${PYTHON_BIN}</string>
      <string>-m</string>
      <string>coinbase_bot.bot</string>
      <string>--mode</string>
      <string>live</string>
      <string>--loop</string>
      <string>--sleep-seconds</string>
      <string>${SLEEP_SECONDS}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${REPO_DIR}/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/logs/launchd.err.log</string>
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/${PLIST_NAME}"

if [[ -n "${IMESSAGE_TARGET}" ]]; then
  cat > "$STATUS_PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${STATUS_PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
      <string>${PYTHON_BIN}</string>
      <string>-m</string>
      <string>coinbase_bot.status_report</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>${STATUS_REPORT_INTERVAL_SECONDS}</integer>
    <key>StandardOutPath</key>
    <string>${REPO_DIR}/logs/status.out.log</string>
    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/logs/status.err.log</string>
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
PLIST

  launchctl bootout "gui/$(id -u)" "$STATUS_PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$STATUS_PLIST_PATH"
  launchctl kickstart -k "gui/$(id -u)/${STATUS_PLIST_NAME}"
fi

echo "Remote bootstrap complete."
echo "Repo: ${REPO_DIR}"
echo "Env: ${ENV_NAME}"
echo "Python: ${PYTHON_BIN}"
echo "LaunchAgent: ${PLIST_PATH}"
if [[ -n "${IMESSAGE_TARGET}" ]]; then
  echo "Status LaunchAgent: ${STATUS_PLIST_PATH}"
  echo "Status interval: ${STATUS_REPORT_INTERVAL_SECONDS}"
fi
