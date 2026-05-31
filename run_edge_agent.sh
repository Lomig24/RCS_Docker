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

EDGE_ID="${1:-}"
PORT="${2:-18080}"
PER_NODE_PARALLELISM="${PER_NODE_PARALLELISM:-1}"
RUNTIME_MAP_PATH="${RUNTIME_MAP_PATH:-RCS_Docker2.0/runtime_map_25n50e.json}"

if [[ -z "$EDGE_ID" ]]; then
  echo "Usage: bash RCS_Docker2.0/run_edge_agent.sh <edge_id> [port]"
  echo "Example: bash RCS_Docker2.0/run_edge_agent.sh pi1 18080"
  exit 2
fi

echo "[run_edge_agent] EDGE_ID=$EDGE_ID PORT=$PORT"
echo "[run_edge_agent] PER_NODE_PARALLELISM=$PER_NODE_PARALLELISM"
echo "[run_edge_agent] RUNTIME_MAP_PATH=$RUNTIME_MAP_PATH"

exec "$PYTHON_EXE" RCS_Docker2.0/edge_agent.py \
  --edge-id "$EDGE_ID" \
  --port "$PORT" \
  --flag 25N50E \
  --topo-config-path eval/benchmarks/Topo4MEC/data/25N50E/config.json \
  --ingress-path eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt \
  --runtime-map-path "$RUNTIME_MAP_PATH" \
  --task-timeout-sec 3.0 \
  --per-node-parallelism "$PER_NODE_PARALLELISM" \
  --real-task-profile cpu-heavy \
  --real-task-base-sec 1.8 \
  --real-task-payload-scale 1.0 \
  --real-task-max-ops 96
