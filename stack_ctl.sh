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
  command -v python >/dev/null 2>&1 && { echo "python"; return 0; }
  echo "python3"
}

PYTHON_EXE="$(resolve_python)"

SCENARIOS=("25N50E" "50N50E" "75N150E" "100N150E" "MilanCityCenter")

usage() {
  cat <<'EOF'
Usage:
  ./stack_ctl.sh regen <scenario|all>     # generate runtime_map + compose
  ./stack_ctl.sh up <scenario|all>        # docker compose up -d
  ./stack_ctl.sh down <scenario|all>      # docker compose down
  ./stack_ctl.sh status <scenario|all>    # count running prefixed containers
  ./stack_ctl.sh run-exp <scenario|all> [-- <experiment_args...>]
                                        # run experiment.py for one/all scenarios
  ./stack_ctl.sh all-run [-- <experiment_args...>]
                                        # equivalent to run-exp all
  ./stack_ctl.sh clean-legacy             # remove old unprefixed node* containers

Scenarios:
  25N50E | 50N50E | 75N150E | 100N150E | MilanCityCenter | all
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

expected_count_for() {
  local flag="$1"
  case "$flag" in
    25N50E) echo 25 ;;
    50N50E) echo 50 ;;
    75N150E) echo 75 ;;
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
    docker compose -f "$file" up -d
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

cmd_clean_legacy() {
  local names
  names="$(docker ps -a --format '{{.Names}}' | grep -E '^node[0-9]+$' || true)"
  if [[ -z "$names" ]]; then
    echo "[clean-legacy] no legacy node* containers found"
    return 0
  fi
  echo "[clean-legacy] removing legacy containers:"
  echo "$names"
  # shellcheck disable=SC2086
  docker rm -f $names
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
elif [[ "$action" != "clean-legacy" && "$action" != "all-run" ]]; then
  target="${1:-all}"
fi

if [[ "$action" == "run-exp" && $# -ge 1 && "$1" == "--" ]]; then
  shift || true
fi

if [[ "$action" != "clean-legacy" && "$action" != "all-run" ]]; then
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
  clean-legacy)
    cmd_clean_legacy
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
