#!/bin/bash
# Upload private pipeline inputs with no sandbox compute; keep this Mac awake.
set -euo pipefail
TASK_SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ "${1:-}" == "--background" ]]; then
  shift
  uv run --project "$TASK_SCRIPT_DIR" python - "$TASK_SCRIPT_DIR/upload_overnight.sh" "$@" <<'PYLAUNCH'
import subprocess
import sys
from pathlib import Path
log = Path('/tmp/ark-upload-launch.log')
with log.open('ab') as output:
    process = subprocess.Popen(
        ['/bin/bash', sys.argv[1], *sys.argv[2:]],
        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True,
    )
print(f'Upload launched independently: PID {process.pid}')
print(f'Launcher log: {log}')
PYLAUNCH
  exit 0
fi
# The supervisor remains awake across retry backoffs, not just during transfers.
if [[ "${ARK_UPLOAD_AWAKE:-}" != "1" && -x /usr/bin/caffeinate ]]; then
  export ARK_UPLOAD_AWAKE=1
  exec /usr/bin/caffeinate -ims /bin/bash "$0" "$@"
fi
TASK_STATE_DIR="$TASK_SCRIPT_DIR/results/cloud-storage"
mkdir -p "$TASK_STATE_DIR"
TASK_UPLOAD_LOG="$TASK_STATE_DIR/upload-$(date -u +%Y%m%d-%H%M%S).log"
printf 'Upload log: %s\n' "$TASK_UPLOAD_LOG"
printf '%s\n' 'Connect power and leave the external drive attached. No sandbox will be started.'
exec >> "$TASK_UPLOAD_LOG" 2>&1
TASK_EXIT_CODE=1
for TASK_ATTEMPT in 1 2 3 4 5; do
  printf 'Upload attempt %s/5\n' "$TASK_ATTEMPT"
  if uv run --project "$TASK_SCRIPT_DIR" python "$TASK_SCRIPT_DIR/scripts/cloud_storage.py" upload-inputs "$@"; then
    exit 0
  else
    TASK_EXIT_CODE=$?
  fi
  if [[ "$TASK_EXIT_CODE" -eq 2 || "$TASK_ATTEMPT" -eq 5 ]]; then
    printf 'Upload stopped with exit %s. See the error above. Committed files are preserved.\n' "$TASK_EXIT_CODE"
    exit "$TASK_EXIT_CODE"
  fi
  TASK_RETRY_DELAY=$((TASK_ATTEMPT * 60))
  printf 'Transfer interrupted. Retrying in %s seconds; committed files will be reused.\n' "$TASK_RETRY_DELAY"
  sleep "$TASK_RETRY_DELAY"
done
exit "$TASK_EXIT_CODE"
