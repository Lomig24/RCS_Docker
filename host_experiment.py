import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from statistics import mean
from typing import Dict, List, Optional, Tuple

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from adaptor_v4 import ModelRealAdaptorV2
from strategy_v4 import ModelStrategy
from topology_v4 import Topology25N50E


@dataclass
class EdgeHostLink:
    edge_id: str
    base_url: str
    host_uplink_mbps: float = 150.0
    host_downlink_mbps: float = 150.0
    host_rtt_ms: float = 8.0
    gateway_node: int = 0


@dataclass
class HostConfig:
    scenario_name: str = "3Pi-25N50E"
    topo_config_path: str = "eval/benchmarks/Topo4MEC/data/25N50E/config.json"
    ingress_path: str = "eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt"
    tasks_path: str = "eval/benchmarks/Topo4MEC/data/75N150E/testset.csv"
    model_path: str = "logs/train/25N50E/aux_sa_A0/ckps/ckp_epoch0.tar"
    model_kind: str = "auto"
    n_tasks: int = 300
    strategies: Tuple[str, ...] = ("ModelBased", "Random", "RoundRobin", "GreedyLatency")
    status_refresh_interval: int = 3
    task_timeout_sec: float = 3.0
    edge_call_timeout_sec: float = 0.0
    real_task_profile: str = "cpu-heavy"
    real_task_base_sec: float = 1.8
    real_task_payload_scale: float = 1.0
    real_task_max_ops: int = 96
    real_task_repeat: int = 1
    real_task_target_sec: float = 0.0
    real_task_ddl_factor: float = 1.8
    real_task_ddl_min_margin: float = 2.5
    real_task_use_dataset_ddl: bool = False
    result_payload_kb: float = 8.0
    model_latency_penalty: float = 0.015
    dispatch_mode: str = "serial"  # serial | parallel
    dispatch_parallelism: int = 1
    dispatch_per_edge: int = 1
    dispatch_per_node: int = 1
    progress_interval: int = 10
    trace_output_path: str = "RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl"
    output_json_path: str = "RCS_Docker2.0/outputs/2.0/json/results_v20.json"
    summary_csv_path: str = "RCS_Docker2.0/outputs/2.0/csv/test_result_v20.csv"


def resolve_input_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    candidates = [
        os.path.join(os.getcwd(), path),
        os.path.join(PROJECT_ROOT, path),
        os.path.join(CURRENT_DIR, path),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join(PROJECT_ROOT, path)


def resolve_output_path(path: str) -> str:
    if os.path.isabs(path):
        out = path
    else:
        out = os.path.join(PROJECT_ROOT, path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return out


def http_get_json(url: str, timeout: float = 5.0) -> Dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def http_post_json(url: str, payload: Dict, timeout: float = 10.0) -> Dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def load_tasks(path: str, n_tasks: int) -> List[Dict[str, float]]:
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


class EdgeContext:
    def __init__(self, link: EdgeHostLink, topo: Topology25N50E, model_path: str):
        self.link = link
        self.topo = topo
        self.adaptor = ModelRealAdaptorV2(
            n_nodes=topo.n_nodes,
            normalized_distance_matrix=topo.hops_norm,
            node_attr_max=topo.node_attr_max,
        )
        self.model = ModelStrategy(
            n_nodes=topo.n_nodes,
            model_path=model_path,
            model_kind=getattr(link, "model_kind", "auto"),
        )
        self.node_offset = 0


@dataclass
class PendingDispatch:
    edge: EdgeContext
    task: Dict[str, float]
    local_node: int
    status: Dict[str, float]
    extra: Dict[str, float]
    t_up: float
    t_local: float
    t_down: float
    ddl_eff: float
    est_compute: float
    selected_container: str
    global_node: int


class DistributedModelExperimentV40:
    def __init__(self, cfg: HostConfig, edge_links: List[EdgeHostLink]):
        self.cfg = cfg
        self.edge_links = edge_links
        self.topo = Topology25N50E(cfg.topo_config_path, ingress_path=cfg.ingress_path)
        self.tasks = load_tasks(cfg.tasks_path, cfg.n_tasks)
        for e in edge_links:
            setattr(e, "model_kind", cfg.model_kind)
        self.edges: List[EdgeContext] = [EdgeContext(link=e, topo=self.topo, model_path=cfg.model_path) for e in edge_links]

        for i, edge in enumerate(self.edges):
            edge.node_offset = i * self.topo.n_nodes

        self.global_n_nodes = len(self.edges) * self.topo.n_nodes
        self.round_robin_cursor = -1
        self.cached_status_by_edge: Dict[str, List[Dict[str, float]]] = {}

        self.trace_fp = None
        self.results_by_strategy: Dict[str, List[Dict]] = {}

    def close(self):
        if self.trace_fp is not None:
            self.trace_fp.close()
            self.trace_fp = None

    def emit_trace(self, payload: Dict):
        if not self.cfg.trace_output_path:
            return
        if self.trace_fp is None:
            self.trace_fp = open(self.cfg.trace_output_path, "w", encoding="utf-8")
        self.trace_fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.trace_fp.flush()

    def fetch_edge_statuses(self, force: bool = False, step: int = 0):
        if (not force) and (step % max(self.cfg.status_refresh_interval, 1) != 0) and self.cached_status_by_edge:
            return

        for edge in self.edges:
            url = edge.link.base_url.rstrip("/") + "/node_statuses"
            try:
                resp = http_get_json(url, timeout=5.0)
                if not resp.get("ok"):
                    raise RuntimeError(resp.get("error", "edge returned not ok"))
                self.cached_status_by_edge[edge.link.edge_id] = resp["data"]["statuses"]
            except Exception:
                # Network blip fallback: all idle.
                self.cached_status_by_edge[edge.link.edge_id] = [
                    {
                        "node_id": i,
                        "free_cpu_freq": self.topo.nodes[i].max_cpu_freq,
                        "free_buffer_size": self.topo.nodes[i].max_buffer_size,
                        "idle_energy_coef": self.topo.nodes[i].idle_energy_coef,
                        "exe_energy_coef": self.topo.nodes[i].exe_energy_coef,
                        "cpu_usage": 0.0,
                        "queue_ratio": 0.0,
                    }
                    for i in range(self.topo.n_nodes)
                ]

    def real_task_profile_spec(self, task: Dict[str, float]) -> Dict[str, float]:
        profile = (self.cfg.real_task_profile or "cpu-heavy").strip().lower()
        payload_scale = max(float(self.cfg.real_task_payload_scale), 0.1)

        if profile == "light":
            ops0, sort0, payload0, base0 = 14, 3200, 48 * 1024, 0.9
        elif profile == "balanced":
            ops0, sort0, payload0, base0 = 26, 6800, 96 * 1024, 1.35
        else:
            ops0, sort0, payload0, base0 = 44, 12800, 160 * 1024, 1.95

        band = task["task_id"] % 3
        amp = 0.8 if band == 0 else (1.0 if band == 1 else 1.25)

        target_sec = max(float(self.cfg.real_task_target_sec), 0.0)
        base_sec_cfg = max(self.cfg.real_task_base_sec, 0.2)
        base_sec = base0 * amp * (base_sec_cfg / max(1.95, 1e-6))
        ops = int(max(2, min(ops0 * amp, self.cfg.real_task_max_ops)))
        sort_n = int(max(1200, min(sort0 * amp, 42000)))
        payload_base = max(8 * 1024, min(payload0 * amp, 256 * 1024))
        payload_bytes = int(min(payload_base * payload_scale, 64 * 1024 * 1024))
        repeat = max(int(self.cfg.real_task_repeat), 1)

        if target_sec > 0:
            base_sec = target_sec

        return {
            "ops": ops,
            "sort_n": sort_n,
            "payload_bytes": payload_bytes,
            "base_sec": float(base_sec * repeat),
            "repeat": repeat,
            "target_sec": target_sec,
        }

    def estimate_compute(self, local_node_id: int, status: Dict[str, float], task: Dict[str, float]) -> float:
        spec = self.real_task_profile_spec(task)
        free_cpu = max(float(status["free_cpu_freq"]), 1e-6)
        node_cpu = max(float(self.topo.nodes[local_node_id].max_cpu_freq), 1e-6)
        saturation_penalty = min(max(node_cpu / max(free_cpu, 1e-6), 1.0), 4.0)
        normalized = node_cpu / max(free_cpu, 1e-6)
        return float(spec["base_sec"] * normalized * saturation_penalty)

    def estimate_transfer(self, edge: EdgeContext, local_node_id: int, task: Dict[str, float]) -> Tuple[float, float, float]:
        # Host -> edge uplink.
        up_bps = max(edge.link.host_uplink_mbps * 1_000_000.0, 1e-6)
        down_bps = max(edge.link.host_downlink_mbps * 1_000_000.0, 1e-6)
        task_bits = max(float(task["task_size"]), 0.0)
        result_bits = max(float(self.cfg.result_payload_kb) * 8.0 * 1024.0, 1.0)
        half_rtt = max(edge.link.host_rtt_ms, 0.0) / 2000.0

        host_to_edge = (task_bits / min(max(task["trans_bit_rate"], 1e-6), up_bps)) + half_rtt
        local_hop = self.topo.estimate_transfer_time(
            task_size=task_bits,
            trans_bit_rate=min(max(task["trans_bit_rate"], 1e-6), up_bps),
            src_id=int(edge.link.gateway_node),
            dst_id=local_node_id,
        )
        edge_to_host = (result_bits / down_bps) + half_rtt
        return host_to_edge, local_hop, edge_to_host

    def effective_ddl(self, task: Dict[str, float], transfer_sum: float, estimated_compute: float) -> float:
        if self.cfg.real_task_use_dataset_ddl:
            return float(task["ddl"])
        return float(
            transfer_sum
            + max(estimated_compute * self.cfg.real_task_ddl_factor, 0.1)
            + max(self.cfg.real_task_ddl_min_margin, 0.0)
        )

    def _node_available(
        self,
        edge: EdgeContext,
        local_node: int,
        pending_by_node: Optional[Dict[Tuple[str, int], int]],
        per_node_limit: int,
    ) -> bool:
        if pending_by_node is None:
            return True
        key = (edge.link.edge_id, int(local_node))
        return int(pending_by_node.get(key, 0)) < max(int(per_node_limit), 1)

    def choose_target(
        self,
        strategy: str,
        task: Dict[str, float],
        pending_by_node: Optional[Dict[Tuple[str, int], int]] = None,
        per_node_limit: int = 1,
    ) -> Tuple[EdgeContext, int, Dict[str, float]]:
        if strategy == "Random":
            candidates: List[Tuple[EdgeContext, int]] = []
            for edge in self.edges:
                for local in range(self.topo.n_nodes):
                    if self._node_available(edge, local, pending_by_node, per_node_limit):
                        candidates.append((edge, local))
            if candidates:
                edge, local = random.choice(candidates)
            else:
                edge = random.choice(self.edges)
                local = random.randint(0, self.topo.n_nodes - 1)
            return edge, local, {}

        if strategy == "RoundRobin":
            for _ in range(self.global_n_nodes):
                self.round_robin_cursor = (self.round_robin_cursor + 1) % self.global_n_nodes
                edge_idx = self.round_robin_cursor // self.topo.n_nodes
                local = self.round_robin_cursor % self.topo.n_nodes
                edge = self.edges[edge_idx]
                if self._node_available(edge, local, pending_by_node, per_node_limit):
                    return edge, local, {}
            self.round_robin_cursor = (self.round_robin_cursor + 1) % self.global_n_nodes
            edge_idx = self.round_robin_cursor // self.topo.n_nodes
            local = self.round_robin_cursor % self.topo.n_nodes
            return self.edges[edge_idx], local, {}

        if strategy == "GreedyLatency":
            best = (None, None, float("inf"))
            for edge in self.edges:
                statuses = self.cached_status_by_edge[edge.link.edge_id]
                for local in range(self.topo.n_nodes):
                    if not self._node_available(edge, local, pending_by_node, per_node_limit):
                        continue
                    status = statuses[local]
                    est_compute = self.estimate_compute(local, status, task)
                    t1, t2, t3 = self.estimate_transfer(edge, local, task)
                    total = t1 + t2 + est_compute + t3
                    if total < best[2]:
                        best = (edge, local, total)
            if best[0] is None or best[1] is None:
                for edge in self.edges:
                    statuses = self.cached_status_by_edge[edge.link.edge_id]
                    for local in range(self.topo.n_nodes):
                        status = statuses[local]
                        est_compute = self.estimate_compute(local, status, task)
                        t1, t2, t3 = self.estimate_transfer(edge, local, task)
                        total = t1 + t2 + est_compute + t3
                        if total < best[2]:
                            best = (edge, local, total)
            assert best[0] is not None and best[1] is not None
            return best[0], best[1], {"pred_latency": best[2]}

        # Model-based hierarchical: local model per edge, then choose highest Q with latency regularization.
        best_edge = None
        best_local = 0
        best_score = float("-inf")
        best_extra: Dict[str, float] = {}
        for edge in self.edges:
            statuses = self.cached_status_by_edge[edge.link.edge_id]
            src = int(edge.link.gateway_node)
            model_input = edge.adaptor.build_model_input(task=task, task_src_node_id=src, all_node_statuses=statuses)
            local, extra = edge.model.select_node(task, src, statuses, model_input, edge.topo)
            q_values = extra.get("q_values", [])
            if q_values:
                scored_candidates: List[Tuple[float, int]] = []
                for i in range(min(len(q_values), self.topo.n_nodes)):
                    if self._node_available(edge, i, pending_by_node, per_node_limit):
                        scored_candidates.append((float(q_values[i]), i))
                if scored_candidates:
                    _, local = max(scored_candidates, key=lambda x: x[0])
            if not self._node_available(edge, local, pending_by_node, per_node_limit):
                continue

            q_val = float(q_values[local]) if local < len(q_values) else 0.0
            est_compute = self.estimate_compute(local, statuses[local], task)
            t1, t2, t3 = self.estimate_transfer(edge, local, task)
            pred_total = t1 + t2 + est_compute + t3
            score = q_val - self.cfg.model_latency_penalty * pred_total
            if score > best_score:
                best_score = score
                best_edge = edge
                best_local = local
                best_extra = {
                    "model_q": q_val,
                    "model_score": score,
                    "pred_total": pred_total,
                }

        if best_edge is None:
            for edge in self.edges:
                statuses = self.cached_status_by_edge[edge.link.edge_id]
                src = int(edge.link.gateway_node)
                model_input = edge.adaptor.build_model_input(task=task, task_src_node_id=src, all_node_statuses=statuses)
                local, extra = edge.model.select_node(task, src, statuses, model_input, edge.topo)
                q_values = extra.get("q_values", [])
                q_val = float(q_values[local]) if local < len(q_values) else 0.0
                est_compute = self.estimate_compute(local, statuses[local], task)
                t1, t2, t3 = self.estimate_transfer(edge, local, task)
                pred_total = t1 + t2 + est_compute + t3
                score = q_val - self.cfg.model_latency_penalty * pred_total
                if score > best_score:
                    best_score = score
                    best_edge = edge
                    best_local = local
                    best_extra = {
                        "model_q": q_val,
                        "model_score": score,
                        "pred_total": pred_total,
                    }

        assert best_edge is not None
        return best_edge, best_local, best_extra

    def call_edge_run_task(self, edge: EdgeContext, local_node_id: int, task: Dict[str, float]) -> Dict:
        payload = {
            "local_node_id": local_node_id,
            "timeout_sec": self.cfg.task_timeout_sec,
            "task": {
                "task_id": int(task["task_id"]),
                "real_task_profile": self.cfg.real_task_profile,
                "real_task_base_sec": self.cfg.real_task_base_sec,
                "real_task_payload_scale": self.cfg.real_task_payload_scale,
                "real_task_max_ops": self.cfg.real_task_max_ops,
                "real_task_repeat": self.cfg.real_task_repeat,
                "real_task_target_sec": self.cfg.real_task_target_sec,
            },
        }
        url = edge.link.base_url.rstrip("/") + "/run_task"
        call_timeout = float(self.cfg.edge_call_timeout_sec)
        if call_timeout <= 0:
            call_timeout = max(self.cfg.task_timeout_sec + 1.5, 2.0)
        try:
            resp = http_post_json(url, payload, timeout=call_timeout)
            if not resp.get("ok"):
                return {"ok": False, "compute_time": 0.0, "error": resp.get("error", "edge not ok")}
            return resp.get("data", {"ok": False, "compute_time": 0.0, "error": "missing data"})
        except urllib.error.URLError as e:
            return {"ok": False, "compute_time": 0.0, "error": f"network:{e}"}
        except Exception as e:
            return {"ok": False, "compute_time": 0.0, "error": str(e)}

    def classify_error(self, error: str) -> str:
        e = str(error or "").strip()
        if not e:
            return "ok"
        low = e.lower()
        if e == "UnknownFailure":
            return "unknown_failure"
        if e == "TimeoutError":
            return "ddl_timeout"
        if "connection reset by peer" in low or "network:" in low or "connection refused" in low:
            return "network_error"
        if "is not running" in low or "no such container" in low:
            return "container_unavailable"
        if "out of memory" in low or "oom" in low or "cannot allocate memory" in low:
            return "memory_overload"
        if "timeout" in low:
            return "exec_timeout"
        return "other_error"

        self.fetch_edge_statuses(force=True, step=0)
        for strategy in self.cfg.strategies:
            rows: List[Dict] = []
            start = time.time()
            self.round_robin_cursor = -1
            completed = 0

            parallel_enabled = (
                str(self.cfg.dispatch_mode).strip().lower() == "parallel"
                and int(self.cfg.dispatch_parallelism) > 1
            )

            def _finalize(dispatch: PendingDispatch, run_res: Dict):
                nonlocal completed
                compute_real = float(run_res.get("compute_time", 0.0))
                wall_exec = float(run_res.get("wall_time_sec", compute_real))
                queue_wait = float(run_res.get("queue_wait_sec", 0.0))
                total_latency = (dispatch.t_up + dispatch.t_local + dispatch.t_down) + wall_exec

                ok_edge = bool(run_res.get("ok", False))
                within_ddl = total_latency <= dispatch.ddl_eff
                success = bool(ok_edge and within_ddl)
                error = ""
                if not ok_edge:
                    error = str(run_res.get("error", "edge_error"))
                elif not within_ddl:
                    error = "TimeoutError"
                elif (not success) and (not error):
                    error = "UnknownFailure"

                row = {
                    "strategy": strategy,
                    "task_id": int(dispatch.task["task_id"]),
                    "src_node": str(dispatch.task["src_name"]),
                    "selected_edge": dispatch.edge.link.edge_id,
                    "selected_local_node": int(dispatch.local_node),
                    "selected_global_node": int(dispatch.global_node),
                    "selected_container": dispatch.selected_container,
                    "success": success,
                    "error": error,
                    "error_type": self.classify_error(error),
                    "latency": float(total_latency),
                    "ddl": float(dispatch.ddl_eff),
                    "transfer_time": float(dispatch.t_up + dispatch.t_local + dispatch.t_down),
                    "compute_time": float(compute_real),
                    "edge_wall_time": float(wall_exec),
                    "edge_queue_wait": float(queue_wait),
                    "pred_compute_time": float(dispatch.est_compute),
                    "meta": dispatch.extra,
                }
                rows.append(row)

                self.emit_trace(
                    {
                        "event": "offload_decision",
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "strategy": strategy,
                        "task_id": int(dispatch.task["task_id"]),
                        "task_name": dispatch.task["task_name"],
                        "selected_edge": dispatch.edge.link.edge_id,
                        "selected_local_node": int(dispatch.local_node),
                        "selected_global_node": int(dispatch.global_node),
                        "selected_container": dispatch.selected_container,
                        "selected_node_status": dispatch.status,
                        "success": success,
                        "error": error,
                        "runtime": {
                            "transfer_up": dispatch.t_up,
                            "transfer_local": dispatch.t_local,
                            "transfer_down": dispatch.t_down,
                            "compute": compute_real,
                            "edge_wall_time": wall_exec,
                            "edge_queue_wait": queue_wait,
                            "total": total_latency,
                            "ddl_eff": dispatch.ddl_eff,
                        },
                    }
                )

                completed += 1
                if completed % max(self.cfg.progress_interval, 1) == 0:
                    elapsed = max(time.time() - start, 1e-6)
                    succ_lat = [r["latency"] for r in rows if r.get("success")]
                    avg_succ_lat = mean(succ_lat) if succ_lat else 0.0
                    print(
                        f"[{strategy}] {completed}/{len(self.tasks)} done | succ={sum(1 for r in rows if r['success'])} "
                        f"| avg_lat={avg_succ_lat:.3f}s | elapsed={elapsed:.1f}s",
                        flush=True,
                    )

            if parallel_enabled:
                max_workers = max(int(self.cfg.dispatch_parallelism), 1)
                per_edge_limit = max(int(self.cfg.dispatch_per_edge), 1)
                per_node_limit = max(int(self.cfg.dispatch_per_node), 1)
                pending: Dict[Future, PendingDispatch] = {}
                pending_by_edge: Dict[str, int] = {e.link.edge_id: 0 for e in self.edges}
                pending_by_node: Dict[Tuple[str, int], int] = {}

                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="host-dispatch") as executor:
                    for i, task in enumerate(self.tasks):
                        self.fetch_edge_statuses(force=False, step=i)
                        edge, local_node, extra = self.choose_target(
                            strategy=strategy,
                            task=task,
                            pending_by_node=pending_by_node,
                            per_node_limit=per_node_limit,
                        )

                        # Respect edge-level inflight cap; wait for completions when saturated.
                        while (
                            len(pending) >= max_workers
                            or pending_by_edge.get(edge.link.edge_id, 0) >= per_edge_limit
                        ):
                            done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
                            for fut in done:
                                dispatch = pending.pop(fut)
                                pending_by_edge[dispatch.edge.link.edge_id] = max(
                                    pending_by_edge.get(dispatch.edge.link.edge_id, 1) - 1,
                                    0,
                                )
                                node_key = (dispatch.edge.link.edge_id, int(dispatch.local_node))
                                pending_by_node[node_key] = max(int(pending_by_node.get(node_key, 1)) - 1, 0)
                                try:
                                    run_res = fut.result()
                                except Exception as ex:
                                    run_res = {"ok": False, "compute_time": 0.0, "error": str(ex)}
                                _finalize(dispatch, run_res)

                        statuses = self.cached_status_by_edge[edge.link.edge_id]
                        status = statuses[local_node]

                        est_compute = self.estimate_compute(local_node, status, task)
                        t_up, t_local, t_down = self.estimate_transfer(edge, local_node, task)
                        transfer_sum = t_up + t_local + t_down
                        ddl_eff = self.effective_ddl(task, transfer_sum, est_compute)

                        global_node = edge.node_offset + local_node
                        selected_container = f"{edge.link.edge_id}:{edge.topo.id_to_name(local_node)}"
                        dispatch = PendingDispatch(
                            edge=edge,
                            task=task,
                            local_node=local_node,
                            status=status,
                            extra=extra,
                            t_up=t_up,
                            t_local=t_local,
                            t_down=t_down,
                            ddl_eff=ddl_eff,
                            est_compute=est_compute,
                            selected_container=selected_container,
                            global_node=global_node,
                        )

                        fut = executor.submit(self.call_edge_run_task, edge, local_node, task)
                        pending[fut] = dispatch
                        pending_by_edge[edge.link.edge_id] = pending_by_edge.get(edge.link.edge_id, 0) + 1
                        node_key = (edge.link.edge_id, int(local_node))
                        pending_by_node[node_key] = int(pending_by_node.get(node_key, 0)) + 1

                    # Drain remaining inflight tasks.
                    for fut, dispatch in list(pending.items()):
                        pending_by_edge[dispatch.edge.link.edge_id] = max(
                            pending_by_edge.get(dispatch.edge.link.edge_id, 1) - 1,
                            0,
                        )
                        node_key = (dispatch.edge.link.edge_id, int(dispatch.local_node))
                        pending_by_node[node_key] = max(int(pending_by_node.get(node_key, 1)) - 1, 0)
                        try:
                            run_res = fut.result()
                        except Exception as ex:
                            run_res = {"ok": False, "compute_time": 0.0, "error": str(ex)}
                        _finalize(dispatch, run_res)
            else:
                for i, task in enumerate(self.tasks):
                    self.fetch_edge_statuses(force=False, step=i)
                    edge, local_node, extra = self.choose_target(strategy=strategy, task=task)
                    statuses = self.cached_status_by_edge[edge.link.edge_id]
                    status = statuses[local_node]

                    est_compute = self.estimate_compute(local_node, status, task)
                    t_up, t_local, t_down = self.estimate_transfer(edge, local_node, task)
                    transfer_sum = t_up + t_local + t_down
                    ddl_eff = self.effective_ddl(task, transfer_sum, est_compute)

                    run_res = self.call_edge_run_task(edge, local_node, task)

                    global_node = edge.node_offset + local_node
                    selected_container = f"{edge.link.edge_id}:{edge.topo.id_to_name(local_node)}"
                    dispatch = PendingDispatch(
                        edge=edge,
                        task=task,
                        local_node=local_node,
                        status=status,
                        extra=extra,
                        t_up=t_up,
                        t_local=t_local,
                        t_down=t_down,
                        ddl_eff=ddl_eff,
                        est_compute=est_compute,
                        selected_container=selected_container,
                        global_node=global_node,
                    )
                    _finalize(dispatch, run_res)

            self.results_by_strategy[strategy] = rows

        self.close()

    def build_summary(self) -> Dict[str, Dict[str, float]]:
        summary: Dict[str, Dict[str, float]] = {}
        for strategy, rows in self.results_by_strategy.items():
            if not rows:
                summary[strategy] = {
                    "n_tasks": 0,
                    "success_rate": 0.0,
                    "avg_latency": 0.0,
                    "timeout_error": 0,
                    "network_error": 0,
                    "container_unavailable": 0,
                    "memory_overload": 0,
                    "other_error": 0,
                }
                continue
            success_cnt = sum(1 for r in rows if r["success"])
            succ_lat = [r["latency"] for r in rows if r.get("success")]
            lat = mean(succ_lat) if succ_lat else 0.0
            timeout_cnt = sum(1 for r in rows if r.get("error_type") == "ddl_timeout")
            network_cnt = sum(1 for r in rows if r.get("error_type") == "network_error")
            container_down_cnt = sum(1 for r in rows if r.get("error_type") == "container_unavailable")
            memory_overload_cnt = sum(1 for r in rows if r.get("error_type") == "memory_overload")
            other_err_cnt = sum(
                1 for r in rows if r.get("error_type") in ("exec_timeout", "other_error", "unknown_failure")
            )
            summary[strategy] = {
                "n_tasks": len(rows),
                "success_rate": success_cnt * 100.0 / max(len(rows), 1),
                "avg_latency": float(lat),
                "timeout_error": int(timeout_cnt),
                "network_error": int(network_cnt),
                "container_unavailable": int(container_down_cnt),
                "memory_overload": int(memory_overload_cnt),
                "other_error": int(other_err_cnt),
            }
        return summary

    def write_outputs(self):
        payload = {
            "version": "4.0",
            "scenario": self.cfg.scenario_name,
            "config": {
                "n_tasks": self.cfg.n_tasks,
                "strategies": list(self.cfg.strategies),
                "task_timeout_sec": self.cfg.task_timeout_sec,
                "edge_call_timeout_sec": self.cfg.edge_call_timeout_sec,
                "real_task_profile": self.cfg.real_task_profile,
                "real_task_base_sec": self.cfg.real_task_base_sec,
                "real_task_payload_scale": self.cfg.real_task_payload_scale,
                "real_task_max_ops": self.cfg.real_task_max_ops,
                "real_task_repeat": self.cfg.real_task_repeat,
                "real_task_target_sec": self.cfg.real_task_target_sec,
                "model_kind": self.cfg.model_kind,
                "real_task_ddl_factor": self.cfg.real_task_ddl_factor,
                "real_task_ddl_min_margin": self.cfg.real_task_ddl_min_margin,
                "result_payload_kb": self.cfg.result_payload_kb,
                "status_refresh_interval": self.cfg.status_refresh_interval,
                "dispatch_mode": self.cfg.dispatch_mode,
                "dispatch_parallelism": self.cfg.dispatch_parallelism,
                "dispatch_per_edge": self.cfg.dispatch_per_edge,
                "dispatch_per_node": self.cfg.dispatch_per_node,
            },
            "edges": [
                {
                    "edge_id": e.link.edge_id,
                    "base_url": e.link.base_url,
                    "host_uplink_mbps": e.link.host_uplink_mbps,
                    "host_downlink_mbps": e.link.host_downlink_mbps,
                    "host_rtt_ms": e.link.host_rtt_ms,
                    "gateway_node": e.link.gateway_node,
                }
                for e in self.edges
            ],
            "summary": self.build_summary(),
            "results": self.results_by_strategy,
        }
        with open(self.cfg.output_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        with open(self.cfg.summary_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Strategy",
                    "SuccessRate",
                    "AveLatSuccessOnly",
                    "TimeoutError",
                    "NetworkError",
                    "ContainerUnavailable",
                    "MemoryOverload",
                    "OtherError",
                    "NTasks",
                ]
            )
            for k, v in self.build_summary().items():
                writer.writerow(
                    [
                        k,
                        f"{v['success_rate']:.4f}",
                        f"{v['avg_latency']:.6f}",
                        v["timeout_error"],
                        v["network_error"],
                        v["container_unavailable"],
                        v["memory_overload"],
                        v["other_error"],
                        v["n_tasks"],
                    ]
                )


def load_host_config(path: str) -> Tuple[HostConfig, List[EdgeHostLink]]:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)

    cfg = HostConfig(
        scenario_name=d.get("scenario_name", "3Pi-25N50E"),
        topo_config_path=resolve_input_path(d.get("topo_config_path", "eval/benchmarks/Topo4MEC/data/25N50E/config.json")),
        ingress_path=resolve_input_path(d.get("ingress_path", "eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt")),
        tasks_path=resolve_input_path(d.get("tasks_path", "eval/benchmarks/Topo4MEC/data/75N150E/testset.csv")),
        model_path=resolve_input_path(d.get("model_path", "logs/train/25N50E/aux_sa_A0/ckps/ckp_epoch0.tar")),
        model_kind=str(d.get("model_kind", "auto")),
        n_tasks=int(d.get("n_tasks", 300)),
        strategies=tuple(d.get("strategies", ["ModelBased", "Random", "RoundRobin", "GreedyLatency"])),
        status_refresh_interval=int(d.get("status_refresh_interval", 3)),
        task_timeout_sec=float(d.get("task_timeout_sec", 3.0)),
        edge_call_timeout_sec=float(d.get("edge_call_timeout_sec", 0.0)),
        real_task_profile=d.get("real_task_profile", "cpu-heavy"),
        real_task_base_sec=float(d.get("real_task_base_sec", 1.8)),
        real_task_payload_scale=float(d.get("real_task_payload_scale", 1.0)),
        real_task_max_ops=int(d.get("real_task_max_ops", 96)),
        real_task_repeat=int(d.get("real_task_repeat", 1)),
        real_task_target_sec=float(d.get("real_task_target_sec", 0.0)),
        real_task_ddl_factor=float(d.get("real_task_ddl_factor", 1.8)),
        real_task_ddl_min_margin=float(d.get("real_task_ddl_min_margin", 2.5)),
        real_task_use_dataset_ddl=bool(d.get("real_task_use_dataset_ddl", False)),
        result_payload_kb=float(d.get("result_payload_kb", 8.0)),
        model_latency_penalty=float(d.get("model_latency_penalty", 0.015)),
        dispatch_mode=str(d.get("dispatch_mode", "serial")),
        dispatch_parallelism=int(d.get("dispatch_parallelism", 1)),
        dispatch_per_edge=int(d.get("dispatch_per_edge", 1)),
        dispatch_per_node=int(d.get("dispatch_per_node", 1)),
        progress_interval=int(d.get("progress_interval", 10)),
        trace_output_path=resolve_output_path(d.get("trace_output_path", "RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl")),
        output_json_path=resolve_output_path(d.get("output_json_path", "RCS_Docker2.0/outputs/2.0/json/results_v20.json")),
        summary_csv_path=resolve_output_path(d.get("summary_csv_path", "RCS_Docker2.0/outputs/2.0/csv/test_result_v20.csv")),
    )

    links: List[EdgeHostLink] = []
    for raw in d.get("edges", []):
        links.append(
            EdgeHostLink(
                edge_id=str(raw["edge_id"]),
                base_url=str(raw["base_url"]),
                host_uplink_mbps=float(raw.get("host_uplink_mbps", 150.0)),
                host_downlink_mbps=float(raw.get("host_downlink_mbps", 150.0)),
                host_rtt_ms=float(raw.get("host_rtt_ms", 8.0)),
                gateway_node=int(raw.get("gateway_node", 0)),
            )
        )

    if len(links) != 3:
        raise ValueError("v4.0 host config requires exactly 3 edges for this deployment plan")

    return cfg, links


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v4.0 host orchestrator (3 Raspberry Pis x 25 nodes)")
    p.add_argument("--config", type=str, default="RCS_Docker2.0/host_config_example.json")
    p.add_argument("--n-tasks", type=int, default=-1)
    return p.parse_args()


def main():
    args = parse_args()
    cfg, edge_links = load_host_config(resolve_input_path(args.config))
    if args.n_tasks > 0:
        cfg.n_tasks = args.n_tasks

    print(f"[host] scenario={cfg.scenario_name}", flush=True)
    print(f"[host] edges={', '.join([e.edge_id for e in edge_links])}", flush=True)
    print(f"[host] tasks={cfg.tasks_path} n={cfg.n_tasks}", flush=True)

    exp = DistributedModelExperimentV40(cfg, edge_links)
    exp.run()
    exp.write_outputs()

    summary = exp.build_summary()
    print("\n===== v4.0 summary =====", flush=True)
    for k, v in summary.items():
        print(
            f"{k:14s} | success={v['success_rate']:.2f}% | avg_latency={v['avg_latency']:.4f}s | timeout={v['timeout_error']}",
            flush=True,
        )
    print(f"json: {cfg.output_json_path}", flush=True)
    print(f"csv : {cfg.summary_csv_path}", flush=True)


if __name__ == "__main__":
    main()
