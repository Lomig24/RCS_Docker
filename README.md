![logo](./RCS_Docker_logo.png)
# Ver1.0

[中文版本](README_CN.md)

RCS_Docker_ver1.0 replaces the simulated execution layer in [RayCloudSim](https://github.com/ZhangRui111/RayCloudSim) with Docker-based runtime nodes. It is designed for task generation and strategy evaluation on the Topo4MEC benchmark.

This version already includes a complete Docker-adapted evaluation workflow, including topology-aware transmission, resource-constrained execution simulation, and strategy comparison. Real Docker workloads are not yet fully integrated, but the framework is ready for future extensions.

The dependencies listed in this directory do not include all core RayCloudSim requirements. Please complete the RayCloudSim environment setup first.

## Available Heuristic Strategies

- Random
- RoundRobin
- GreedyLatency

## Feature Overview

- Generate `runtime_map` and `docker-compose` files automatically for each scenario.
- Generate tasks according to `GenerationTime` in `testset.csv`.
- Estimate transmission time using shortest-path routing and bottleneck bandwidth.
- Simulate execution, queueing, and timeout failures under CPU/Buffer constraints.
- Export detailed JSON results and aggregated CSV summaries for reproducible experiments.

## Supported Scenarios

- 25N50E
- 50N50E
- 100N150E
- MilanCityCenter

## Project Structure

- `topology.py`: Topology loading, shortest paths, bandwidth estimation, ingress handling.
- `monitor.py`: Runtime sampling of Docker container and host resource status.
- `strategies.py`: Heuristic strategy implementations.
- `generate_compose.py`: Scenario-based generation of compose files and runtime maps.
- `experiment.py`: Main entry for task generation and offloading evaluation.
- `stack_ctl.sh`: One-command flow for regenerate/start/run/stop.
- `requirements.txt`: Python dependencies for this Docker workflow.

## Prerequisites

- Python 3.9+
- Docker Engine 20.10+ (with `docker compose` support)
- Existing RayCloudSim Topo4MEC dataset directories:
	- `eval/benchmarks/Topo4MEC/data/<flag>`
	- `eval/benchmarks/Topo4MEC/source/<flag>`

## Installation

Run from repository root:

```bash
cd RCS_Docker
python -m pip install -r requirements.txt
```

## Quick Start

### Option 1: Run Commands Directly

```bash
cd RCS_Docker
python generate_compose.py --flag 50N50E
docker compose -f docker-compose.50n50e.yml up -d
python experiment.py --flag 50N50E --execution-mode docker --n-tasks 200
docker compose -f docker-compose.50n50e.yml down
```

### Option 2: Use the Helper Script

```bash
cd RCS_Docker
chmod +x stack_ctl.sh
./stack_ctl.sh regen 50N50E
./stack_ctl.sh up 50N50E
./stack_ctl.sh run-exp 50N50E -- --execution-mode docker --n-tasks 200
./stack_ctl.sh down 50N50E
```

## Common Experiment Arguments

`experiment.py` frequently used arguments:

- `--flag`: Scenario name. One of `25N50E`, `50N50E`, `100N150E`, `MilanCityCenter`.
- `--execution-mode`: `simulate` or `docker`.
	- `simulate`: Static full-resource simulation without runtime container polling.
	- `docker`: Uses live container resource observations.
- `--n-tasks`: Number of generated tasks. `-1` means the full test set.
- `--strategies`: Comma-separated strategy list, for example `Random,RoundRobin,GreedyLatency`.
- `--runtime-map-path`: Custom runtime map path.
- `--output-path`: Custom JSON output path.
- `--summary-csv-path`: Custom CSV summary output path.

## Outputs

Default output paths:

- `RCS_Docker/outputs/json/<flag>/results_v10.json`
- `RCS_Docker/outputs/csv/<flag>/test_result_v10.csv`

Summary metrics include:

- `success_rate`
- `avg_latency`, `p50_latency`, `p95_latency`
- `NetCongestionError`, `InsufficientBufferError`, `TimeoutError`

## Relationship to RayCloudSim

- Topology and dataset inputs are loaded from `eval/benchmarks/Topo4MEC`.
- This module focuses on Docker-based execution replacement and baseline strategy evaluation.
- It does not include training pipelines, private strategy implementations, or model inference workflows.

