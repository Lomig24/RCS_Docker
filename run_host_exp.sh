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

if [[ "$N_TASKS" == "-1" ]]; then
  exec "$PYTHON_EXE" RCS_Docker2.0/host_experiment.py --config "$CONFIG_PATH"
else
  exec "$PYTHON_EXE" RCS_Docker2.0/host_experiment.py --config "$CONFIG_PATH" --n-tasks "$N_TASKS"
fi
