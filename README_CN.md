![logo](./RCS_Docker_logo.png)
# RCS_Docker2.0

[English Version](README.md)

RCS_Docker2.0 聚焦真实任务处理与 3 Pi x 25 节点边缘协同评估。

该版本提供主机调度、边缘真实容器执行、实时监控和策略对比流程。

## 功能概览

- 主机侧统一调度 3 个 edge agent，支持 model-based/Random/RoundRobin/GreedyLatency。
- 边缘侧在目标容器执行真实任务，并回传执行结果与资源占用情况。
- 支持基线对比、跨场景模型对比、RL 模型对比三类实验入口。
- 提供实时可视化监控（CPU/内存/边缘任务计数）。
- 输出 JSON 明细、CSV 汇总和 trace 轨迹文件。

## 项目结构

- `host_experiment.py`：主机任务调度与实验主流程。
- `edge_agent.py`：边缘 HTTP 服务与真实任务执行。
- `visualize_offload_live_4.py`：实时资源与任务分布可视化。
- `run_host_exp.sh`：主机实验启动脚本。
- `run_host_with_live.sh`：主机实验 + 实时可视化。
- `run_edge_agent.sh`：边缘代理启动脚本。
- `run_compare_baselines_live.sh`：model-based 对比启发式基线。
- `run_compare_scenarios_live.sh`：model-based 跨场景模型对比。
- `run_compare_rlmodels_live.sh`：model-based/MAML/DQN 对比。
- `host_config_example.json`：标准配置模板。
- `host_config_compute_heavy.json`：高负载配置模板。
- `runtime_map_25n50e.json`：默认 runtime map 示例。

## 环境要求

- Python 3.9+
- Docker Engine 20.10+（支持 `docker compose`）
- RayCloudSim 数据目录可用：
  - `eval/benchmarks/Topo4MEC/data/<flag>`
  - `eval/benchmarks/Topo4MEC/source/<flag>`

## 安装依赖

在仓库根目录执行：

```bash
cd RCS_Docker2.0
python -m pip install -r requirements.txt
```

## 快速开始

### 1) 每台边缘设备启动容器栈

```bash
cd RCS_Docker2.0
./stack_ctl.sh regen 25N50E
./stack_ctl.sh up 25N50E
./stack_ctl.sh status 25N50E
```

### 2) 每台边缘设备启动 edge agent

```bash
cd /path/to/repo
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi1 18080
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi2 18080
PER_NODE_PARALLELISM=1 bash RCS_Docker2.0/run_edge_agent.sh pi3 18080
```

可选环境变量：
- `RUNTIME_MAP_PATH`：默认 `RCS_Docker2.0/runtime_map_25n50e.json`
- `PER_NODE_PARALLELISM`：单节点并发任务数

### 3) 配置主机侧参数

编辑 `RCS_Docker2.0/host_config_example.json`：

- 将 `edges[*].base_url` 改为真实边缘地址
- 根据网络环境调整 `host_uplink_mbps`、`host_downlink_mbps`、`host_rtt_ms`
- 设置 `model_path` 指向可用 checkpoint

### 4) 运行主机实验

```bash
cd /path/to/repo
bash RCS_Docker2.0/run_host_exp.sh RCS_Docker2.0/host_config_example.json 300
```

默认输出：

- `RCS_Docker2.0/outputs/2.0/json/results_v20.json`
- `RCS_Docker2.0/outputs/2.0/csv/test_result_v20.csv`
- `RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl`

### 5) 运行实时可视化

```bash
cd /path/to/repo
python RCS_Docker2.0/visualize_offload_live_4.py \
  --config RCS_Docker2.0/host_config_example.json \
  --trace-path RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl \
  --refresh-sec 1.0
```

## 对比实验脚本

- `run_compare_baselines_live.sh`：model-based vs Random/RoundRobin/GreedyLatency
- `run_compare_scenarios_live.sh`：model-based 跨训练场景模型
- `run_compare_rlmodels_live.sh`：model-based vs MAML vs DQN

以上脚本默认产出到 `RCS_Docker2.0/outputs/2.0/compare_runs/`。

## 说明

- 本版本不包含训练流程，仅用于部署与评估。
