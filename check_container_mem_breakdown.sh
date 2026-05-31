#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash RCS_Docker2.0/check_container_mem_breakdown.sh 25n50e_node18
#   bash RCS_Docker2.0/check_container_mem_breakdown.sh 25n50e_node18 5

CONTAINER="${1:-25n50e_node18}"
REPEAT="${2:-1}"

for ((i=1; i<=REPEAT; i++)); do
  echo "=== sample ${i}/${REPEAT} container=${CONTAINER} ==="
  docker stats "$CONTAINER" --no-stream --format 'stats: {{.CPUPerc}}, {{.MemUsage}}'

  docker exec "$CONTAINER" sh -lc '
    if [[ -f /sys/fs/cgroup/memory.current ]]; then
      echo "memory.current: $(cat /sys/fs/cgroup/memory.current)"
    fi
    if [[ -f /sys/fs/cgroup/memory.stat ]]; then
      echo "memory.stat key fields:";
      grep -E "^(anon|file|inactive_file|active_file|slab|kernel) " /sys/fs/cgroup/memory.stat || true
    else
      echo "memory.stat not found in container cgroup"
    fi
  '

  if [[ "$i" -lt "$REPEAT" ]]; then
    sleep 1
  fi
done

cat <<'EOF'
Interpretation hints:
- file/active_file high + anon low: mostly page cache, not process heap growth.
- anon high and growing: actual process memory pressure.
- If cache dominates, use mem-mode=delta in visualizer to compare dynamic pressure.
EOF
