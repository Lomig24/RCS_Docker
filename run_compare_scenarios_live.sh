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
EPOCH="${3:--1}"

# live args
REFRESH_SEC="${4:-0.5}"
COUNT_STRATEGY="${5:-ModelBased}"
RESOURCE_SAMPLES="${6:-1}"
RESOURCE_GAP_MS="${7:-0}"
MEM_MODE="${8:-raw}"
EDGE_TIMEOUT_SEC="${9:-10}"
CPU_HOLD_SEC="${10:-0.8}"
ACTIVE_HOLD_MS="${11:-3000}"

RUN_TAG="scenarios_$(date +%Y%m%d_%H%M%S)"
BASE_OUT="RCS_Docker2.0/outputs/2.0/compare_runs/scenarios/${RUN_TAG}"
CFG_DIR="$BASE_OUT/configs"
mkdir -p "$CFG_DIR" "$BASE_OUT/json" "$BASE_OUT/csv" "$BASE_OUT/trace"
SUMMARY_CSV="$BASE_OUT/summary_scenarios.csv"

"$PYTHON_EXE" - <<'PY' "$SUMMARY_CSV"
import csv, sys, os
p = sys.argv[1]
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        'scenario_model','model_path','epoch','n_tasks','success_rate','avg_latency',
        'timeout_error','network_error','container_unavailable','memory_overload','other_error'
    ])
PY

for SCEN in 25N50E 50N50E 100N150E MilanCityCenter; do
  if [[ "$SCEN" == "25N50E" && -n "${MODEL_25N50E_PATH:-}" ]]; then
    MODEL_PATH="$MODEL_25N50E_PATH"
  elif [[ "$SCEN" == "50N50E" && -n "${MODEL_50N50E_PATH:-}" ]]; then
    MODEL_PATH="$MODEL_50N50E_PATH"
  elif [[ "$SCEN" == "100N150E" && -n "${MODEL_100N150E_PATH:-}" ]]; then
    MODEL_PATH="$MODEL_100N150E_PATH"
  elif [[ "$SCEN" == "MilanCityCenter" && -n "${MODEL_MILAN_PATH:-}" ]]; then
    MODEL_PATH="$MODEL_MILAN_PATH"
  else
    MODEL_PATH="$($PYTHON_EXE - <<'PY' "$SCEN" "$EPOCH"
import os, re, sys
scen, epoch_s = sys.argv[1], sys.argv[2]
ckp_dir = os.path.join('logs','train',scen,'aux_sa_A0','ckps')
epoch = int(epoch_s)
if epoch >= 0:
    p = os.path.join(ckp_dir, f'ckp_epoch{epoch}.tar')
    print(p if os.path.exists(p) else '')
else:
    if not os.path.isdir(ckp_dir):
        print('')
    else:
        files = [x for x in os.listdir(ckp_dir) if x.startswith('ckp_epoch') and x.endswith('.tar')]
        def num(n):
            m = re.search(r'ckp_epoch(\\d+)\\.tar$', n)
            return int(m.group(1)) if m else -1
        files.sort(key=num)
        print(os.path.join(ckp_dir, files[-1]) if files else '')
PY
)"
  fi

  if [[ -z "$MODEL_PATH" ]]; then
    echo "[compare-scenarios] missing checkpoint for $SCEN" >&2
    exit 2
  fi

  CFG_PATH="$CFG_DIR/${SCEN}.json"
  "$PYTHON_EXE" - <<'PY' "$BASE_CONFIG" "$CFG_PATH" "$SCEN" "$MODEL_PATH" "$BASE_OUT"
import json, os, re, sys
src, dst, scen, model_path, base_out = sys.argv[1:6]
with open(src, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
  cfg['scenario_name'] = f"{cfg.get('scenario_name','v40')}-scenario-{scen}"
  cfg['strategies'] = ['ModelBased']
cfg['model_path'] = model_path
cfg['model_kind'] = 'aux_sa'
tag = scen
cfg['trace_output_path'] = f"{base_out}/trace/{tag}.jsonl"
cfg['output_json_path'] = f"{base_out}/json/{tag}.json"
cfg['summary_csv_path'] = f"{base_out}/csv/{tag}.csv"
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
print(dst)
PY

  echo "[compare-scenarios] running $SCEN model=$MODEL_PATH"
  bash RCS_Docker2.0/run_host_with_live.sh \
    "$CFG_PATH" "$N_TASKS" "$REFRESH_SEC" "$COUNT_STRATEGY" \
    "$RESOURCE_SAMPLES" "$RESOURCE_GAP_MS" "$MEM_MODE" "$EDGE_TIMEOUT_SEC" \
    "$CPU_HOLD_SEC" "$ACTIVE_HOLD_MS"

  "$PYTHON_EXE" - <<'PY' "$BASE_OUT/json/${SCEN}.json" "$SUMMARY_CSV" "$SCEN" "$MODEL_PATH"
import csv, json, re, sys
json_path, summary_csv, scen, model_path = sys.argv[1:5]
with open(json_path, 'r', encoding='utf-8') as f:
    d = json.load(f)
    s = d.get('summary', {}).get('ModelBased', {})
m = re.search(r'ckp_epoch(\d+)\.tar$', model_path)
ep = int(m.group(1)) if m else ''
with open(summary_csv, 'a', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        scen, model_path, ep, s.get('n_tasks',0), s.get('success_rate',0.0), s.get('avg_latency',0.0),
        s.get('timeout_error',0), s.get('network_error',0), s.get('container_unavailable',0),
        s.get('memory_overload',0), s.get('other_error',0)
    ])
PY

done

echo "[compare-scenarios] done summary=$SUMMARY_CSV"
