#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

resolve_python() {
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    echo "${VIRTUAL_ENV}/bin/python"
    return 0
  fi
  command -v python3 >/dev/null 2>&1 && { echo "python3"; return 0; }
  echo "python"
}

PYTHON_EXE="$(resolve_python)"
SCENARIOS=("25N50E" "50N50E" "100N150E" "MilanCityCenter")

usage() {
  cat <<'EOF'
Usage:
  ./stack_ctl.sh regen <scenario|all>     # generate runtime_map + compose
  ./stack_ctl.sh up <scenario|all>        # docker compose up -d
  ./stack_ctl.sh down <scenario|all>      # docker compose down
  ./stack_ctl.sh status <scenario|all>    # count running prefixed containers
  ./stack_ctl.sh run-exp <scenario|all> [-- <experiment_args...>]
  ./stack_ctl.sh all-run [-- <experiment_args...>]   # equivalent to run-exp all

Scenarios:
  25N50E | 50N50E | 100N150E | MilanCityCenter | all
EOF
}

is_valid_scenario() {
  local flag="$1"
  for s in "${SCENARIOS[@]}"; do
    if [[ "$s" == "$flag" ]]; then
      return 0
    fi
  done
  return 1
}

expand_targets() {
  local target="$1"
  if [[ "$target" == "all" ]]; then
    printf '%s\n' "${SCENARIOS[@]}"
    return 0
  fi
  if is_valid_scenario "$target"; then
    printf '%s\n' "$target"
    return 0
  fi
  return 1
}

compose_file_for() {
  local flag="$1"
  local lower
  lower="$(echo "$flag" | tr '[:upper:]' '[:lower:]')"
  echo "docker-compose.${lower}.yml"
}

cleanup_conflicting_names_in_compose() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    return 0
  fi

  local removed=0
  local cname
  while IFS= read -r cname; do
    [[ -z "$cname" ]] && continue
    if docker ps -aq --filter "name=^/${cname}$" | grep -q .; then
      echo "[up] removing existing container with same name: ${cname}"
      docker rm -f "$cname" >/dev/null
      removed=1
    fi
  done < <(awk '/container_name:/ {print $2}' "$file")

  return "$removed"
}

compose_up_with_retry() {
  local file="$1"
  local output
  local code

  set +e
  output="$(docker compose -f "$file" up -d --remove-orphans 2>&1)"
  code=$?
  set -e

  if [[ $code -eq 0 ]]; then
    [[ -n "$output" ]] && echo "$output"
    return 0
  fi

  echo "$output"
  if echo "$output" | grep -q "is already in use by container"; then
    echo "[up] detected container-name conflict, cleaning and retrying..."
    cleanup_conflicting_names_in_compose "$file" || true
    docker compose -f "$file" up -d --remove-orphans
    return 0
  fi

  return "$code"
}

expected_count_for() {
  local flag="$1"
  case "$flag" in
    25N50E) echo 25 ;;
    50N50E) echo 50 ;;
    100N150E) echo 100 ;;
    MilanCityCenter) echo 30 ;;
    *) echo 0 ;;
  esac
}

running_count_for() {
  local flag="$1"
  local prefix
  prefix="$(echo "$flag" | tr '[:upper:]' '[:lower:]')"
  docker ps --format '{{.Names}}' | grep -E "^${prefix}_node[0-9]+$" | wc -l || true
}

cmd_regen() {
  local target="$1"
  while IFS= read -r flag; do
    echo "[regen] ${flag}"
    "$PYTHON_EXE" generate_compose.py --flag "$flag"
  done < <(expand_targets "$target")
}

cmd_up() {
  local target="$1"
  while IFS= read -r flag; do
    local file
    file="$(compose_file_for "$flag")"
    echo "[up] ${flag} -> ${file}"
    compose_up_with_retry "$file"
  done < <(expand_targets "$target")
}

cmd_down() {
  local target="$1"
  while IFS= read -r flag; do
    local file
    file="$(compose_file_for "$flag")"
    echo "[down] ${flag} -> ${file}"
    docker compose -f "$file" down
  done < <(expand_targets "$target")
}

cmd_status() {
  local target="$1"
  while IFS= read -r flag; do
    local actual expected
    actual="$(running_count_for "$flag")"
    expected="$(expected_count_for "$flag")"
    echo "[status] ${flag}: ${actual}/${expected} running"
  done < <(expand_targets "$target")
}

cmd_run_exp() {
  local target="$1"
  shift || true
  local extra=("$@")

  while IFS= read -r flag; do
    echo "[run-exp] ${flag}"
    "$PYTHON_EXE" experiment.py --flag "$flag" "${extra[@]}"
  done < <(expand_targets "$target")
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

action="$1"
shift || true

target="all"
if [[ "$action" == "run-exp" ]]; then
  if [[ $# -ge 1 && "$1" != "--" ]]; then
    target="$1"
    shift || true
  fi
elif [[ "$action" != "all-run" ]]; then
  target="${1:-all}"
fi

if [[ "$action" == "run-exp" && $# -ge 1 && "$1" == "--" ]]; then
  shift || true
fi

if [[ "$action" != "all-run" ]]; then
  if ! expand_targets "$target" >/dev/null; then
    echo "Invalid scenario: $target"
    usage
    exit 2
  fi
fi

case "$action" in
  regen)
    cmd_regen "$target"
    ;;
  up)
    cmd_up "$target"
    ;;
  down)
    cmd_down "$target"
    ;;
  status)
    cmd_status "$target"
    ;;
  run-exp)
    cmd_run_exp "$target" "$@"
    ;;
  all-run)
    if [[ $# -ge 1 && "$1" == "--" ]]; then
      shift || true
    fi
    cmd_run_exp "all" "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
