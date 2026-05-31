#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/.."

resolve_python() {
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    echo "${VIRTUAL_ENV}/bin/python"
    return 0
  fi
  command -v python3 >/dev/null 2>&1 && { echo "python3"; return 0; }
  command -v python >/dev/null 2>&1 && { echo "python"; return 0; }
  echo "python3"
}

PYTHON_EXE="$(resolve_python)"

CONFIG_PATH="${1:-RCS_Docker2.0/host_config_example.json}"
N_TASKS="${2:--1}"
REFRESH_SEC="${3:-1.0}"
COUNT_STRATEGY="${4:-ModelBased}"
RESOURCE_SAMPLES="${5:-2}"
RESOURCE_GAP_MS="${6:-40}"
MEM_MODE="${7:-delta}"
EDGE_TIMEOUT_SEC="${8:-8}"
CPU_HOLD_SEC="${9:-3.0}"
ACTIVE_HOLD_MS="${10:-2000}"

TRACE_PATH="$($PYTHON_EXE - <<'PY' "$CONFIG_PATH"
import json, os, sys
cfg = sys.argv[1]
with open(cfg, 'r', encoding='utf-8') as f:
    d = json.load(f)
print(d.get('trace_output_path', 'RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl'))
PY
)"

mkdir -p "$(dirname "$TRACE_PATH")"
: > "$TRACE_PATH"

echo "[preflight] config=$CONFIG_PATH"
echo "[preflight] trace=$TRACE_PATH"

$PYTHON_EXE - <<'PY' "$CONFIG_PATH"
import json
import sys
import urllib.request

cfg = sys.argv[1]
with open(cfg, 'r', encoding='utf-8') as f:
    d = json.load(f)

ok = True
for e in d.get('edges', []):
    eid = e.get('edge_id')
    base = str(e.get('base_url', '')).rstrip('/')
    try:
        with urllib.request.urlopen(base + '/health', timeout=3) as r:
            data = json.loads(r.read().decode('utf-8'))
        print(f"[preflight] {eid} health ok={data.get('ok')}")
    except Exception as ex:
        ok = False
        print(f"[preflight] {eid} health failed: {ex}")

if not ok:
    raise SystemExit(2)
PY

echo "[live] starting visualizer..."
"$PYTHON_EXE" RCS_Docker2.0/visualize_offload_live_4.py \
  --config "$CONFIG_PATH" \
  --trace-path "$TRACE_PATH" \
  --refresh-sec "$REFRESH_SEC" \
  --count-strategy "$COUNT_STRATEGY" \
  --resource-samples "$RESOURCE_SAMPLES" \
  --resource-gap-ms "$RESOURCE_GAP_MS" \
  --edge-timeout-sec "$EDGE_TIMEOUT_SEC" \
  --cpu-hold-sec "$CPU_HOLD_SEC" \
  --active-hold-ms "$ACTIVE_HOLD_MS" \
  --mem-mode "$MEM_MODE" \
  --count-window-lines 10000 &
VIS_PID=$!

cleanup() {
  if kill -0 "$VIS_PID" >/dev/null 2>&1; then
    kill "$VIS_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

echo "[exp] starting host experiment..."
if [[ "$N_TASKS" == "-1" ]]; then
  "$PYTHON_EXE" RCS_Docker2.0/host_experiment.py --config "$CONFIG_PATH"
else
  "$PYTHON_EXE" RCS_Docker2.0/host_experiment.py --config "$CONFIG_PATH" --n-tasks "$N_TASKS"
fi

echo "[done] experiment finished, visualizer stopped."
