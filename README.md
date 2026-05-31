![logo](./RCS_Docker_logo.png)
# RCS_Docker2.0

[中文版本](README_CN.md)

RCS_Docker2.0 focuses on real task execution and distributed edge evaluation with 3 Raspberry Pi workers (25 containers per Pi).

This release keeps the host orchestrator, edge real-workload runner, live visualization, and comparison scripts.

## Feature Overview

- Distributed host scheduling across 3 edge agents.
- Real task execution on selected Docker containers at the edge side.
- End-to-end latency accounting including host-edge and edge-local transfer.
- Live monitoring for CPU, memory, and per-edge task distribution.
- JSON details, CSV summaries, and trace export for reproducible analysis.

## Project Structure

- `host_experiment.py`: Host-side orchestration and experiment pipeline.
- `edge_agent.py`: Edge HTTP service and real task executor.
- `visualize_offload_live_4.py`: Live visualization for resources and offload counts.
- `run_host_exp.sh`: Host experiment launcher.
- `run_host_with_live.sh`: Host experiment + live monitor launcher.
- `run_edge_agent.sh`: Edge agent launcher.
-- `run_compare_baselines_live.sh`: model-based strategy vs Random/RoundRobin/GreedyLatency.
-- `run_compare_scenarios_live.sh`: model-based strategy across training scenarios.
-- `run_compare_rlmodels_live.sh`: model-based strategy vs MAML vs DQN.
- `host_config_example.json`: Standard host config template.
- `host_config_compute_heavy.json`: Compute-heavy config template.
- `runtime_map_25n50e.json`: Default runtime-map example.
- `stack_ctl.sh`: Stack control helper for edge deployment.

## Prerequisites

- Python 3.9+
- Docker Engine 20.10+ (with `docker compose` support)
- Existing RayCloudSim Topo4MEC dataset directories:
  - `eval/benchmarks/Topo4MEC/data/<flag>`
  - `eval/benchmarks/Topo4MEC/source/<flag>`

## Installation

Run from repository root:

```bash
cd RCS_Docker2.0
python -m pip install -r requirements.txt
```

## Quick Start

### Step 1: Start 25N50E Docker stack on each edge device

```bash
cd RCS_Docker2.0
./stack_ctl.sh regen 25N50E
./stack_ctl.sh up 25N50E
./stack_ctl.sh status 25N50E
```

### Step 2: Start one edge agent per Raspberry Pi

```bash
cd /path/to/repo
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi1 18080
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi2 18080
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi3 18080
```

Optional environment variables:

- `RUNTIME_MAP_PATH` (default: `RCS_Docker2.0/runtime_map_25n50e.json`)
- `PER_NODE_PARALLELISM` (max concurrent tasks per local container)

### Step 3: Update host config

Edit `RCS_Docker2.0/host_config_example.json`:

- Replace `edges[*].base_url` with your real edge agent URLs.
- Tune `host_uplink_mbps`, `host_downlink_mbps`, `host_rtt_ms`.
- Set `model_path` to a valid checkpoint path.

### Step 4: Run host experiment

```bash
cd /path/to/repo
bash RCS_Docker2.0/run_host_exp.sh RCS_Docker2.0/host_config_example.json 300
```

Default outputs:

- `RCS_Docker2.0/outputs/2.0/json/results_v20.json`
- `RCS_Docker2.0/outputs/2.0/csv/test_result_v20.csv`
- `RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl`

### Step 5: Run live visualization

```bash
cd /path/to/repo
python RCS_Docker2.0/visualize_offload_live_4.py \
  --config RCS_Docker2.0/host_config_example.json \
  --trace-path RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl \
  --refresh-sec 1.0
```

## Comparison Scripts

- `run_compare_baselines_live.sh`
- `run_compare_scenarios_live.sh`
- `run_compare_rlmodels_live.sh`

All comparison outputs are written under `RCS_Docker2.0/outputs/2.0/compare_runs/`.

## Notes

- This release focuses on deployment and evaluation, not model training.
