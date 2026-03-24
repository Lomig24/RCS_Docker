# RCS_Docker_ver1.0

[English Version](README.md)

RCS_Docker_ver1.0 用 Docker 运行时节点替代 [RayCloudSim](https://github.com/ZhangRui111/RayCloudSim) 中的仿真执行层，面向 Topo4MEC 数据集的任务生成与策略评估。

当前版本已具备完整的 Docker 适配评估流程，包括基于拓扑的传输估计、资源约束下的执行模拟与策略对比。真实 Docker workload 尚未全面接入，但整体框架已为后续扩展做好准备。

本目录中的依赖不包含 RayCloudSim 所有核心依赖，请先完成 RayCloudSim 环境配置。

## 可用启发式策略

- Random
- RoundRobin
- GreedyLatency

## 功能概览

- 按场景自动生成 `runtime_map` 与 `docker-compose` 文件。
- 基于 `testset.csv` 中的 `GenerationTime` 生成任务。
- 基于最短路径与路径瓶颈带宽估计传输时间。
- 在 CPU/Buffer 约束下模拟执行、排队与超时失败。
- 输出 JSON 明细与 CSV 汇总，便于复现实验与策略对比。

## 支持场景

- 25N50E
- 50N50E
- 100N150E
- MilanCityCenter

## 项目结构

- `topology.py`：拓扑加载、最短路径、带宽估计与 ingress 处理。
- `monitor.py`：Docker 容器与主机资源状态采样。
- `strategies.py`：启发式策略实现。
- `generate_compose.py`：按场景生成 compose 文件与 runtime_map。
- `experiment.py`：任务生成与任务卸载评估主程序。
- `stack_ctl.sh`：一键执行重建/启动/实验/停止流程。
- `requirements.txt`：本模块 Python 依赖。

## 环境要求

- Python 3.9+
- Docker Engine 20.10+（支持 `docker compose`）
- 已存在 RayCloudSim Topo4MEC 数据目录：
  - `eval/benchmarks/Topo4MEC/data/<flag>`
  - `eval/benchmarks/Topo4MEC/source/<flag>`

## 安装依赖

在仓库根目录执行：

```bash
cd RCS_Docker
python -m pip install -r requirements.txt
```

## 快速开始

### 方式 1：直接命令执行

```bash
cd RCS_Docker
python generate_compose.py --flag 50N50E
docker compose -f docker-compose.50n50e.yml up -d
python experiment.py --flag 50N50E --execution-mode docker --n-tasks 200
docker compose -f docker-compose.50n50e.yml down
```

### 方式 2：脚本一键执行

```bash
cd RCS_Docker
chmod +x stack_ctl.sh
./stack_ctl.sh regen 50N50E
./stack_ctl.sh up 50N50E
./stack_ctl.sh run-exp 50N50E -- --execution-mode docker --n-tasks 200
./stack_ctl.sh down 50N50E
```

## 常用实验参数

`experiment.py` 常用参数如下：

- `--flag`：场景名，可选 `25N50E`、`50N50E`、`100N150E`、`MilanCityCenter`。
- `--execution-mode`：`simulate` 或 `docker`。
  - `simulate`：不读取容器实时状态，使用静态满资源进行仿真。
  - `docker`：读取容器实时资源状态进行执行评估。
- `--n-tasks`：生成任务数，`-1` 表示使用完整测试集。
- `--strategies`：逗号分隔策略列表，例如 `Random,RoundRobin,GreedyLatency`。
- `--runtime-map-path`：自定义 runtime_map 路径。
- `--output-path`：自定义 JSON 输出路径。
- `--summary-csv-path`：自定义 CSV 汇总输出路径。

## 输出结果

默认输出路径：

- `RCS_Docker/outputs/json/<flag>/results_v10.json`
- `RCS_Docker/outputs/csv/<flag>/test_result_v10.csv`

汇总指标包括：

- `success_rate`
- `avg_latency`、`p50_latency`、`p95_latency`
- `NetCongestionError`、`InsufficientBufferError`、`TimeoutError`

## 与 RayCloudSim 的关系

- 拓扑与数据输入来自 `eval/benchmarks/Topo4MEC`。
- 本模块聚焦 Docker 执行层替代与基础策略评估。
- 不包含训练流程、私有策略实现与模型推理流程。
