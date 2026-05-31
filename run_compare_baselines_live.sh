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

BASE_CONFIG="${1:-RCS_Docker2.0/host_config_compute_heavy.json}"
N_TASKS="${2:-300}"
MODEL_PATH="${3:-}"
MODEL_KIND="${4:-auto}"

# live args (same order as run_host_with_live.sh)
REFRESH_SEC="${5:-0.5}"
COUNT_STRATEGY="${6:-ModelBased}"
RESOURCE_SAMPLES="${7:-1}"
RESOURCE_GAP_MS="${8:-0}"
MEM_MODE="${9:-raw}"
EDGE_TIMEOUT_SEC="${10:-10}"
CPU_HOLD_SEC="${11:-0.8}"
ACTIVE_HOLD_MS="${12:-3000}"

RUN_TAG="baselines_$(date +%Y%m%d_%H%M%S)"
CFG_OUT="RCS_Docker2.0/outputs/2.0/compare_runs/configs/${RUN_TAG}.json"

mkdir -p "$(dirname "$CFG_OUT")"

"$PYTHON_EXE" - <<'PY' "$BASE_CONFIG" "$CFG_OUT" "$MODEL_PATH" "$MODEL_KIND"
import json, os, sys
src, dst, model_path, model_kind = sys.argv[1:5]
with open(src, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
cfg['scenario_name'] = f"{cfg.get('scenario_name','v40')}-baselines"
cfg['strategies'] = ['ModelBased', 'Random', 'RoundRobin', 'GreedyLatency']
if model_path.strip():
    cfg['model_path'] = model_path.strip()
cfg['model_kind'] = model_kind.strip() or 'auto'
base = 'RCS_Docker2.0/outputs/2.0/compare_runs/baselines'
os.makedirs(base + '/trace', exist_ok=True)
os.makedirs(base + '/json', exist_ok=True)
os.makedirs(base + '/csv', exist_ok=True)
tag = os.path.splitext(os.path.basename(dst))[0]
cfg['trace_output_path'] = f"{base}/trace/{tag}.jsonl"
cfg['output_json_path'] = f"{base}/json/{tag}.json"
cfg['summary_csv_path'] = f"{base}/csv/{tag}.csv"
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
print(dst)
PY

echo "[compare-baselines] config=$CFG_OUT"

bash RCS_Docker2.0/run_host_with_live.sh \
  "$CFG_OUT" "$N_TASKS" "$REFRESH_SEC" "$COUNT_STRATEGY" \
  "$RESOURCE_SAMPLES" "$RESOURCE_GAP_MS" "$MEM_MODE" "$EDGE_TIMEOUT_SEC" \
  "$CPU_HOLD_SEC" "$ACTIVE_HOLD_MS"

echo "[compare-baselines] done"
