#!/bin/zsh
set -euo pipefail

REPO_DIR="${1:-$HOME/services/coinbase-mean-reversion-bot}"
ENV_NAME="${ENV_NAME:-quant}"
PLIST_NAME="${PLIST_NAME:-com.randomwalkhan.coinbase-bot}"
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

echo "Remote bootstrap complete."
echo "Repo: ${REPO_DIR}"
echo "Env: ${ENV_NAME}"
echo "Python: ${PYTHON_BIN}"
echo "LaunchAgent: ${PLIST_PATH}"

