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

RUN_TAG="rlmodels_$(date +%Y%m%d_%H%M%S)"
BASE_OUT="RCS_Docker2.0/outputs/2.0/compare_runs/rlmodels/${RUN_TAG}"
CFG_DIR="$BASE_OUT/configs"
mkdir -p "$CFG_DIR" "$BASE_OUT/json" "$BASE_OUT/csv" "$BASE_OUT/trace"
SUMMARY_CSV="$BASE_OUT/summary_rlmodels.csv"

"$PYTHON_EXE" - <<'PY' "$SUMMARY_CSV"
import csv, os, sys
p = sys.argv[1]
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        'model_name','model_kind','model_path','epoch','n_tasks','success_rate','avg_latency',
        'timeout_error','network_error','container_unavailable','memory_overload','other_error'
    ])
PY

# name:train_subdir:model_kind:env_override
for ITEM in \
  "ModelBased:aux_sa_A0:aux_sa:MODEL_A_PATH" \
  "MAML:meta_A0:neo:MODEL_MAML_PATH" \
  "DQN:dqn_A0:neo:MODEL_DQN_PATH"
do
  IFS=':' read -r MODEL_NAME SUBDIR MODEL_KIND ENV_NAME <<< "$ITEM"
  OVERRIDE="${!ENV_NAME:-}"

  if [[ -n "$OVERRIDE" ]]; then
    MODEL_PATH="$OVERRIDE"
  else
    MODEL_PATH="$($PYTHON_EXE - <<'PY' "$SUBDIR" "$EPOCH"
import os, re, sys
subdir, epoch_s = sys.argv[1], sys.argv[2]
ckp_dir = os.path.join('logs','train','MilanCityCenter',subdir,'ckps')
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
    echo "[compare-rlmodels] missing checkpoint for $MODEL_NAME" >&2
    exit 2
  fi

  CFG_PATH="$CFG_DIR/${MODEL_NAME}.json"
  "$PYTHON_EXE" - <<'PY' "$BASE_CONFIG" "$CFG_PATH" "$MODEL_NAME" "$MODEL_PATH" "$MODEL_KIND" "$BASE_OUT"
import json, os, sys
src, dst, model_name, model_path, model_kind, base_out = sys.argv[1:7]
with open(src, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
cfg['scenario_name'] = f"{cfg.get('scenario_name','v40')}-rlmodel-{model_name}"
cfg['strategies'] = ['ModelBased']
cfg['model_path'] = model_path
cfg['model_kind'] = model_kind
tag = model_name
cfg['trace_output_path'] = f"{base_out}/trace/{tag}.jsonl"
cfg['output_json_path'] = f"{base_out}/json/{tag}.json"
cfg['summary_csv_path'] = f"{base_out}/csv/{tag}.csv"
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
print(dst)
PY

  echo "[compare-rlmodels] running $MODEL_NAME kind=$MODEL_KIND model=$MODEL_PATH"
  bash RCS_Docker2.0/run_host_with_live.sh \
    "$CFG_PATH" "$N_TASKS" "$REFRESH_SEC" "$COUNT_STRATEGY" \
    "$RESOURCE_SAMPLES" "$RESOURCE_GAP_MS" "$MEM_MODE" "$EDGE_TIMEOUT_SEC" \
    "$CPU_HOLD_SEC" "$ACTIVE_HOLD_MS"

  "$PYTHON_EXE" - <<'PY' "$BASE_OUT/json/${MODEL_NAME}.json" "$SUMMARY_CSV" "$MODEL_NAME" "$MODEL_KIND" "$MODEL_PATH"
import csv, json, re, sys
json_path, summary_csv, model_name, model_kind, model_path = sys.argv[1:6]
with open(json_path, 'r', encoding='utf-8') as f:
    d = json.load(f)
s = d.get('summary', {}).get('ModelBased', {})
m = re.search(r'ckp_epoch(\d+)\.tar$', model_path)
ep = int(m.group(1)) if m else ''
with open(summary_csv, 'a', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        model_name, model_kind, model_path, ep, s.get('n_tasks',0), s.get('success_rate',0.0), s.get('avg_latency',0.0),
        s.get('timeout_error',0), s.get('network_error',0), s.get('container_unavailable',0),
        s.get('memory_overload',0), s.get('other_error',0)
    ])
PY

done

echo "[compare-rlmodels] done summary=$SUMMARY_CSV"
