import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

import numpy as np
from tqdm import tqdm

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from monitor import DockerNodeMonitor
from strategies import GreedyLatencyStrategy, RandomStrategy, RoundRobinStrategy
from topology import TopologyTopo4MEC


SUPPORTED_FLAGS = ("25N50E", "50N50E", "100N150E", "MilanCityCenter")


@dataclass
class RuntimeConfig:
    flag: str = "25N50E"
    topo_config_path: str = "eval/benchmarks/Topo4MEC/data/25N50E/config.json"
    ingress_path: str = "eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt"
    testset_path: str = "eval/benchmarks/Topo4MEC/data/25N50E/testset.csv"
    runtime_map_path: str = "RCS_Docker/runtime_map_25n50e.json"
    output_path: str = "RCS_Docker/outputs/json/25N50E/results_v10.json"
    summary_csv_path: str = "RCS_Docker/outputs/csv/25N50E/test_result_v10.csv"
    n_tasks: int = -1
    strategy_names: Tuple[str, ...] = ("Random", "RoundRobin", "GreedyLatency")
    execution_mode: str = "simulate"  # docker | simulate
    decision_interval_sec: float = 0.0
    status_refresh_interval: int = 20


class Topo4MECDockerExperimentV10:
    def __init__(self, cfg: RuntimeConfig):
        self.cfg = self._normalize_config_paths(cfg)
        self.topology = TopologyTopo4MEC(cfg.topo_config_path, ingress_path=cfg.ingress_path)

        runtime_map = self._load_runtime_map(cfg.runtime_map_path)
        self.node_to_container = {int(k): str(v) for k, v in runtime_map.get("node_to_container", {}).items()}
        if not self.node_to_container:
            self.node_to_container = {i: f"node{i}" for i in range(self.topology.n_nodes)}

        local_node_id = runtime_map.get("local_node_id", None)
        self.local_node_id = int(local_node_id) if isinstance(local_node_id, int) else None
        self.ingress_nodes = set(runtime_map.get("ingress_node_ids", [])) or self.topology.ingress_nodes

        self.monitor = DockerNodeMonitor(node_to_container=self.node_to_container, local_node_id=self.local_node_id)
        self.tasks = self._load_tasks(cfg.testset_path, cfg.n_tasks)
        self.results_by_strategy: Dict[str, List[Dict]] = {}
        self.strategies = self._build_strategies()

    @staticmethod
    def _resolve_input_path(path: str) -> str:
        if os.path.isabs(path):
            return path

        candidates = [
            os.path.join(os.getcwd(), path),
            os.path.join(PROJECT_ROOT, path),
            os.path.join(CURRENT_DIR, path),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return os.path.join(PROJECT_ROOT, path)

    @staticmethod
    def _resolve_output_path(path: str) -> str:
        out = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
        out_dir = os.path.dirname(out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        return out

    def _normalize_config_paths(self, cfg: RuntimeConfig) -> RuntimeConfig:
        cfg.topo_config_path = self._resolve_input_path(cfg.topo_config_path)
        cfg.ingress_path = self._resolve_input_path(cfg.ingress_path)
        cfg.testset_path = self._resolve_input_path(cfg.testset_path)
        cfg.runtime_map_path = self._resolve_input_path(cfg.runtime_map_path)
        cfg.output_path = self._resolve_output_path(cfg.output_path)
        cfg.summary_csv_path = self._resolve_output_path(cfg.summary_csv_path)
        return cfg

    def _build_strategies(self):
        items = {}
        n = self.topology.n_nodes
        for name in self.cfg.strategy_names:
            if name == "Random":
                items[name] = RandomStrategy(n_nodes=n)
            elif name == "RoundRobin":
                items[name] = RoundRobinStrategy(n_nodes=n)
            elif name == "GreedyLatency":
                items[name] = GreedyLatencyStrategy(n_nodes=n)
            else:
                raise ValueError(f"Unsupported strategy: {name}")
        return items

    @staticmethod
    def _load_runtime_map(path: str) -> Dict:
        if not os.path.exists(path):
            return {"local_node_id": None, "node_to_container": {}}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _load_tasks(path: str, n_tasks: int) -> List[Dict[str, float]]:
        rows: List[Dict[str, float]] = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if n_tasks > 0 and i >= n_tasks:
                    break
                rows.append(
                    {
                        "task_name": row["TaskName"],
                        "generation_time": float(row["GenerationTime"]),
                        "task_id": int(row["TaskID"]),
                        "task_size": float(row["TaskSize"]),
                        "cycles_per_bit": float(row["CyclesPerBit"]),
                        "trans_bit_rate": float(row["TransBitRate"]),
                        "ddl": float(row["DDL"]),
                        "src_name": row["SrcName"],
                    }
                )
        rows.sort(key=lambda x: (x["generation_time"], x["task_id"]))
        return rows

    def _static_statuses_for_simulate(self) -> List[Dict[str, float]]:
        statuses: List[Dict[str, float]] = []
        for i in range(self.topology.n_nodes):
            node = self.topology.nodes[i]
            statuses.append(
                {
                    "node_id": node.node_id,
                    "free_cpu_freq": node.max_cpu_freq,
                    "free_buffer_size": node.max_buffer_size,
                    "idle_energy_coef": node.idle_energy_coef,
                    "exe_energy_coef": node.exe_energy_coef,
                    "cpu_usage": 0.0,
                    "queue_ratio": 0.0,
                }
            )
        return statuses

    @staticmethod
    def _estimated_exec_time(task: Dict[str, float], status: Dict[str, float]) -> float:
        task_units = task["task_size"] * task["cycles_per_bit"]
        free_cpu = max(status["free_cpu_freq"], 1e-6)
        return float(task_units / free_cpu)

    def run_strategy(self, strategy_name: str) -> List[Dict]:
        strategy = self.strategies[strategy_name]
        outputs: List[Dict] = []
        node_available_time = [0.0 for _ in range(self.topology.n_nodes)]
        cached_statuses: Optional[List[Dict[str, float]]] = None

        progress_desc = f"{strategy_name:>14} tasks"
        for idx, task in enumerate(
            tqdm(self.tasks, desc=progress_desc, unit="task", leave=False),
            start=1,
        ):
            if self.cfg.execution_mode == "simulate":
                statuses = self._static_statuses_for_simulate()
            else:
                refresh_interval = max(self.cfg.status_refresh_interval, 1)
                should_refresh = cached_statuses is None or idx == 1 or (idx - 1) % refresh_interval == 0
                if should_refresh:
                    cached_statuses = self.monitor.measure_all(self.topology.nodes)
                statuses = cached_statuses

            src_id = self.topology.name_to_id(task["src_name"])
            selected_node, extra = strategy.select_node(
                task=task,
                src_node_id=src_id,
                node_statuses=statuses,
                model_input=None,
                topology=self.topology,
            )

            path = self.topology.shortest_path(src_id, selected_node)
            path_min_bw = self.topology.path_min_bandwidth(path)
            if task["trans_bit_rate"] > path_min_bw:
                outputs.append(
                    {
                        "strategy": strategy_name,
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "src_node": src_id,
                        "selected_node": selected_node,
                        "success": False,
                        "latency": float("inf"),
                        "error": "NetCongestionError",
                        "extra": extra,
                    }
                )
                continue

            transfer_time = self.topology.estimate_transfer_time(
                task_size=task["task_size"],
                trans_bit_rate=task["trans_bit_rate"],
                src_id=src_id,
                dst_id=selected_node,
            )
            arrival_time = task["generation_time"]
            arrival_at_dst = arrival_time + transfer_time
            wait_time = max(node_available_time[selected_node] - arrival_at_dst, 0.0)

            if transfer_time + wait_time > task["ddl"]:
                outputs.append(
                    {
                        "strategy": strategy_name,
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "src_node": src_id,
                        "selected_node": selected_node,
                        "success": False,
                        "latency": transfer_time + wait_time,
                        "error": "TimeoutError",
                        "extra": extra,
                    }
                )
                continue

            if task["task_size"] > statuses[selected_node]["free_buffer_size"]:
                outputs.append(
                    {
                        "strategy": strategy_name,
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "src_node": src_id,
                        "selected_node": selected_node,
                        "success": False,
                        "latency": transfer_time + wait_time,
                        "error": "InsufficientBufferError",
                        "extra": extra,
                    }
                )
                continue

            compute_time = self._estimated_exec_time(task, statuses[selected_node])
            finish_time = arrival_at_dst + wait_time + compute_time
            node_available_time[selected_node] = finish_time
            total_latency = finish_time - arrival_time

            outputs.append(
                {
                    "strategy": strategy_name,
                    "task_id": task["task_id"],
                    "task_name": task["task_name"],
                    "src_node": src_id,
                    "selected_node": selected_node,
                    "success": True,
                    "latency": float(total_latency),
                    "error": "",
                    "extra": extra,
                    "runtime": {
                        "transfer_time": transfer_time,
                        "wait_time": wait_time,
                        "compute_time": compute_time,
                        "path": path,
                        "path_min_bw": path_min_bw,
                    },
                }
            )
            time.sleep(self.cfg.decision_interval_sec)

        return outputs

    @staticmethod
    def _safe_latency(vals: List[float]) -> Dict[str, float]:
        clean = [v for v in vals if np.isfinite(v)]
        if not clean:
            return {"avg_latency": float("inf"), "p50_latency": float("inf"), "p95_latency": float("inf")}
        return {
            "avg_latency": mean(clean),
            "p50_latency": median(clean),
            "p95_latency": float(np.percentile(clean, 95)),
        }

    def run_all(self) -> Dict[str, Dict[str, float]]:
        summary: Dict[str, Dict[str, float]] = {}
        for name in self.cfg.strategy_names:
            rows = self.run_strategy(name)
            self.results_by_strategy[name] = rows

            success_rows = [r for r in rows if r["success"]]
            lat = [r["latency"] for r in success_rows]
            timeout_cnt = sum(1 for r in rows if r.get("error") == "TimeoutError")
            congestion_cnt = sum(1 for r in rows if r.get("error") == "NetCongestionError")
            buf_cnt = sum(1 for r in rows if r.get("error") == "InsufficientBufferError")
            latency_stats = self._safe_latency(lat)

            summary[name] = {
                "tasks": len(rows),
                "success": len(success_rows),
                "success_rate": (len(success_rows) / len(rows)) if rows else 0.0,
                "avg_latency": latency_stats["avg_latency"],
                "p50_latency": latency_stats["p50_latency"],
                "p95_latency": latency_stats["p95_latency"],
                "timeout_error": timeout_cnt,
                "net_congestion_error": congestion_cnt,
                "insufficient_buffer_error": buf_cnt,
            }

        return summary

    def save_results(self, summary: Dict[str, Dict[str, float]]):
        payload = {
            "version": "1.0",
            "config": {
                "flag": self.cfg.flag,
                "topo_config_path": self.cfg.topo_config_path,
                "ingress_path": self.cfg.ingress_path,
                "testset_path": self.cfg.testset_path,
                "runtime_map_path": self.cfg.runtime_map_path,
                "strategy_names": list(self.cfg.strategy_names),
                "execution_mode": self.cfg.execution_mode,
            },
            "ingress_nodes": sorted(self.ingress_nodes),
            "summary": summary,
            "results": self.results_by_strategy,
        }
        with open(self.cfg.output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def save_summary_csv(self, summary: Dict[str, Dict[str, float]]):
        fields = [
            "strategy",
            "Time",
            "tasks",
            "NetCongestionError",
            "InsufficientBufferError",
            "TimeoutError",
            "SuccessRate",
            "AveLatPerTask",
        ]
        with open(self.cfg.summary_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for strategy, val in summary.items():
                writer.writerow(
                    {
                        "strategy": strategy,
                        "Time": time.strftime("%Y-%m-%d %H:%M"),
                        "tasks": val["tasks"],
                        "NetCongestionError": val["net_congestion_error"],
                        "InsufficientBufferError": val["insufficient_buffer_error"],
                        "TimeoutError": val["timeout_error"],
                        "SuccessRate": f"{val['success_rate']:.4f}",
                        "AveLatPerTask": f"{val['avg_latency']:.4f}",
                    }
                )


def scenario_defaults(flag: str) -> Dict[str, str]:
    return {
        "topo_config_path": f"eval/benchmarks/Topo4MEC/data/{flag}/config.json",
        "ingress_path": f"eval/benchmarks/Topo4MEC/source/{flag}/ingress.txt",
        "testset_path": f"eval/benchmarks/Topo4MEC/data/{flag}/testset.csv",
        "runtime_map_path": f"RCS_Docker/runtime_map_{flag.lower()}.json",
        "output_path": f"RCS_Docker/outputs/json/{flag}/results_v10.json",
        "summary_csv_path": f"RCS_Docker/outputs/csv/{flag}/test_result_v10.csv",
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag", type=str, default="25N50E", choices=list(SUPPORTED_FLAGS))
    parser.add_argument("--n-tasks", type=int, default=-1, help="-1 means replay whole testset.csv")
    parser.add_argument("--output-path", type=str, default="")
    parser.add_argument("--summary-csv-path", type=str, default="")
    parser.add_argument("--runtime-map-path", type=str, default="")
    parser.add_argument("--execution-mode", type=str, default="simulate", choices=["docker", "simulate"])
    parser.add_argument(
        "--strategies",
        type=str,
        default="Random,RoundRobin,GreedyLatency",
        help="comma separated strategy list",
    )
    parser.add_argument("--status-refresh-interval", type=int, default=20)
    parser.add_argument("--decision-interval-sec", type=float, default=0.0)
    return parser.parse_args()


def main():
    args = parse_args()
    defaults = scenario_defaults(args.flag)
    strategy_names = tuple([x.strip() for x in args.strategies.split(",") if x.strip()])

    cfg = RuntimeConfig(
        flag=args.flag,
        topo_config_path=defaults["topo_config_path"],
        ingress_path=defaults["ingress_path"],
        testset_path=defaults["testset_path"],
        n_tasks=args.n_tasks,
        output_path=args.output_path or defaults["output_path"],
        summary_csv_path=args.summary_csv_path or defaults["summary_csv_path"],
        runtime_map_path=args.runtime_map_path or defaults["runtime_map_path"],
        strategy_names=strategy_names,
        execution_mode=args.execution_mode,
        status_refresh_interval=args.status_refresh_interval,
        decision_interval_sec=args.decision_interval_sec,
    )

    exp = Topo4MECDockerExperimentV10(cfg)
    summary = exp.run_all()
    exp.save_results(summary)
    exp.save_summary_csv(summary)

    print("=" * 72)
    print(f"Topo4MEC {args.flag} docker experiment v1.0 completed")
    print("=" * 72)
    for name, val in summary.items():
        print(
            f"{name:>14} | success={val['success']}/{val['tasks']} "
            f"| SR={val['success_rate']:.2%} | avg={val['avg_latency']:.4f} time-unit "
            f"| p50={val['p50_latency']:.4f} time-unit | p95={val['p95_latency']:.4f} time-unit"
        )
    print(f"Saved: {cfg.output_path}")
    print(f"Saved summary csv: {cfg.summary_csv_path}")


if __name__ == "__main__":
    main()
