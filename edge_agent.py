import argparse
import json
import os
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Dict, List, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

import sys

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from monitor_v4 import DockerNodeMonitor
from topology_v4 import Topology25N50E


@dataclass
class EdgeConfig:
    edge_id: str = "pi1"
    flag: str = "25N50E"
    host: str = "0.0.0.0"
    port: int = 18080
    topo_config_path: str = "eval/benchmarks/Topo4MEC/data/25N50E/config.json"
    ingress_path: str = "eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt"
    runtime_map_path: str = "RCS_Docker2.0/runtime_map_25n50e.json"
    task_timeout_sec: float = 3.0
    per_node_parallelism: int = 1
    real_task_profile: str = "cpu-heavy"
    real_task_base_sec: float = 1.8
    real_task_payload_scale: float = 1.0
    real_task_max_ops: int = 96
    real_task_repeat: int = 1
    real_task_target_sec: float = 0.0


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


def load_runtime_map(path: str) -> Dict:
    if not os.path.exists(path):
        return {"local_node_id": None, "ingress_node_ids": [], "node_to_container": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_mem_to_kib(mem_str: str) -> float:
    v = mem_str.strip()
    if not v:
        return 0.0
    num = ""
    unit = ""
    for ch in v:
        if ch.isdigit() or ch == ".":
            num += ch
        elif not ch.isspace():
            unit += ch
    try:
        val = float(num)
    except Exception:
        return 0.0
    if unit == "GiB":
        return val * 1024.0 * 1024.0
    if unit == "MiB":
        return val * 1024.0
    if unit == "KiB":
        return val
    if unit == "GB":
        return val * 1000.0 * 1000.0 * 1000.0 / 1024.0
    if unit == "MB":
        return val * 1000.0 * 1000.0 / 1024.0
    if unit == "kB":
        return val * 1000.0 / 1024.0
    if unit == "B":
        return val / 1024.0
    return 0.0


def docker_stats_bulk(container_names: List[str]) -> Dict[str, Dict[str, float]]:
    if not container_names:
        return {}
    try:
        running_out = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True)
        running = {x.strip() for x in running_out.splitlines() if x.strip()}
    except Exception:
        running = set()

    valid = [c for c in container_names if c in running] if running else list(container_names)
    if not valid:
        return {}

    cmd = ["docker", "stats", *valid, "--no-stream", "--format", "{{.Name}},{{.CPUPerc}},{{.MemUsage}}"]
    try:
        out = subprocess.check_output(cmd, text=True)
    except Exception:
        out = ""

    result: Dict[str, Dict[str, float]] = {}
    for row in out.splitlines():
        row = row.strip()
        if not row:
            continue
        parts = [x.strip() for x in row.split(",", 2)]
        if len(parts) != 3:
            continue
        name, cpu_s, mem_usage = parts
        name = name.strip().lstrip("/")
        try:
            cpu_percent = float(cpu_s.replace("%", ""))
        except Exception:
            cpu_percent = 0.0
        mem_now = "0B"
        mem_total = "0B"
        if "/" in mem_usage:
            mem_now, mem_total = [x.strip() for x in mem_usage.split("/", 1)]
        result[name] = {
            "cpu_percent": max(cpu_percent, 0.0),
            "mem_used_kib": parse_mem_to_kib(mem_now),
            "mem_limit_kib": max(parse_mem_to_kib(mem_total), 1.0),
        }

    # Fallback path: if bulk stats partially/fully failed, sample remaining containers one by one.
    if len(result) < len(valid):
        for cname in valid:
            if cname in result:
                continue
            try:
                row = subprocess.check_output(
                    [
                        "docker",
                        "stats",
                        cname,
                        "--no-stream",
                        "--format",
                        "{{.Name}},{{.CPUPerc}},{{.MemUsage}}",
                    ],
                    text=True,
                ).strip()
            except Exception:
                continue
            if not row:
                continue
            parts = [x.strip() for x in row.split(",", 2)]
            if len(parts) != 3:
                continue
            row_name, cpu_s, mem_usage = parts
            row_name = row_name.strip().lstrip("/")
            try:
                cpu_percent = float(cpu_s.replace("%", ""))
            except Exception:
                cpu_percent = 0.0
            mem_now = "0B"
            mem_total = "0B"
            if "/" in mem_usage:
                mem_now, mem_total = [x.strip() for x in mem_usage.split("/", 1)]
            result[row_name or cname] = {
                "cpu_percent": max(cpu_percent, 0.0),
                "mem_used_kib": parse_mem_to_kib(mem_now),
                "mem_limit_kib": max(parse_mem_to_kib(mem_total), 1.0),
            }
    return result


class EdgeRuntime:
    def __init__(self, cfg: EdgeConfig):
        self.cfg = cfg
        self.runtime_map = load_runtime_map(cfg.runtime_map_path)
        self.node_to_container = {int(k): str(v) for k, v in self.runtime_map.get("node_to_container", {}).items()}
        self.topology = Topology25N50E(cfg.topo_config_path, ingress_path=cfg.ingress_path)
        self.monitor = DockerNodeMonitor(node_to_container=self.node_to_container, local_node_id=None)
        self._node_semaphores: Dict[str, threading.Semaphore] = {
            c: threading.Semaphore(max(int(cfg.per_node_parallelism), 1))
            for c in self.node_to_container.values()
        }
        self._active_by_node: Dict[int, int] = {nid: 0 for nid in self.node_to_container.keys()}
        self._last_active_ts_by_node: Dict[int, float] = {nid: 0.0 for nid in self.node_to_container.keys()}
        self._active_lock = threading.Lock()

    def real_task_profile_spec(self, task: Dict[str, float]) -> Dict[str, float]:
        payload_scale = max(float(task.get("real_task_payload_scale", self.cfg.real_task_payload_scale)), 0.1)
        profile = str(task.get("real_task_profile", self.cfg.real_task_profile)).strip().lower()
        base_sec_cfg = max(float(task.get("real_task_base_sec", self.cfg.real_task_base_sec)), 0.2)
        max_ops = int(task.get("real_task_max_ops", self.cfg.real_task_max_ops))
        repeat = max(int(task.get("real_task_repeat", self.cfg.real_task_repeat)), 1)
        target_sec = max(float(task.get("real_task_target_sec", self.cfg.real_task_target_sec)), 0.0)

        if profile == "light":
            ops0, sort0, payload0, base0 = 14, 3200, 48 * 1024, 0.9
        elif profile == "balanced":
            ops0, sort0, payload0, base0 = 26, 6800, 96 * 1024, 1.35
        else:
            ops0, sort0, payload0, base0 = 44, 12800, 160 * 1024, 1.95

        task_id = int(task.get("task_id", 0))
        band = task_id % 3
        amp = 0.8 if band == 0 else (1.0 if band == 1 else 1.25)

        base_sec = base0 * amp * (base_sec_cfg / max(1.95, 1e-6))
        ops = int(max(2, min(ops0 * amp, max_ops)))
        sort_n = int(max(1200, min(sort0 * amp, 42000)))
        payload_base = max(8 * 1024, min(payload0 * amp, 256 * 1024))
        payload_bytes = int(min(payload_base * payload_scale, 64 * 1024 * 1024))
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

    def run_real_task(self, local_node_id: int, task: Dict[str, float], timeout_sec: float) -> Dict:
        container_name = self.node_to_container.get(local_node_id)
        if not container_name:
            return {
                "ok": False,
                "compute_time": 0.0,
                "wall_time_sec": 0.0,
                "queue_wait_sec": 0.0,
                "error": f"container missing for node {local_node_id}",
            }

        spec = self.real_task_profile_spec(task)
        ops = int(spec["ops"])
        sort_n = int(spec["sort_n"])
        payload_bytes = int(spec["payload_bytes"])
        rounds = int(spec.get("repeat", 1))
        target_sec = float(spec.get("target_sec", 0.0))
        repeat = (payload_bytes // 6) + 1

        script = (
            "import time,hashlib,zlib;"
            f"ops={ops};sort_n={sort_n};rounds={rounds};target={target_sec};"
            f"payload=(b'model4'*{repeat})[:{payload_bytes}];"
            "payload=zlib.decompress(zlib.compress(payload,1));"
            "t=time.time();acc=0\n"
            "done=0\n"
            "while True:\n"
            " for i in range(ops):\n"
            "  h=hashlib.sha256(payload+str(i).encode()).digest()\n"
            "  payload=(h+payload[:64])\n"
            "  acc^=h[0]\n"
            " arr=[((i*131+acc)%1000003) for i in range(sort_n)];arr.sort()\n"
            " done+=1\n"
            " if done>=rounds and (target<=0 or (time.time()-t)>=target):\n"
            "  break\n"
            "print(max(time.time()-t,1e-6))"
        )

        sem = self._node_semaphores.get(container_name)
        if sem is None:
            sem = threading.Semaphore(max(int(self.cfg.per_node_parallelism), 1))
            self._node_semaphores[container_name] = sem

        t0 = time.time()
        with sem:
            t_exec_start = time.time()
            queue_wait_sec = max(t_exec_start - t0, 0.0)
            with self._active_lock:
                self._active_by_node[local_node_id] = self._active_by_node.get(local_node_id, 0) + 1
                self._last_active_ts_by_node[local_node_id] = time.time()
            try:
                p = subprocess.run(
                    ["docker", "exec", container_name, "python", "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=max(timeout_sec, 0.1),
                )
                if p.returncode != 0:
                    return {
                        "ok": False,
                        "compute_time": max(time.time() - t_exec_start, 0.0),
                        "wall_time_sec": max(time.time() - t0, 0.0),
                        "queue_wait_sec": queue_wait_sec,
                        "error": p.stderr.strip(),
                    }
                lines = [x.strip() for x in p.stdout.splitlines() if x.strip()]
                if not lines:
                    return {
                        "ok": True,
                        "compute_time": max(time.time() - t_exec_start, 0.0),
                        "wall_time_sec": max(time.time() - t0, 0.0),
                        "queue_wait_sec": queue_wait_sec,
                        "error": "",
                    }
                return {
                    "ok": True,
                    "compute_time": float(lines[-1]),
                    "wall_time_sec": max(time.time() - t0, 0.0),
                    "queue_wait_sec": queue_wait_sec,
                    "error": "",
                }
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "compute_time": max(time.time() - t_exec_start, 0.0),
                    "wall_time_sec": max(time.time() - t0, 0.0),
                    "queue_wait_sec": queue_wait_sec,
                    "error": "timeout",
                }
            except Exception as e:
                return {
                    "ok": False,
                    "compute_time": max(time.time() - t_exec_start, 0.0),
                    "wall_time_sec": max(time.time() - t0, 0.0),
                    "queue_wait_sec": queue_wait_sec,
                    "error": str(e),
                }
            finally:
                with self._active_lock:
                    self._active_by_node[local_node_id] = max(self._active_by_node.get(local_node_id, 1) - 1, 0)
                    self._last_active_ts_by_node[local_node_id] = time.time()

    def meta(self) -> Dict:
        return {
            "edge_id": self.cfg.edge_id,
            "hostname": socket.gethostname(),
            "flag": self.cfg.flag,
            "n_nodes": self.topology.n_nodes,
            "ingress_nodes": sorted(list(self.topology.ingress_nodes)),
            "node_to_container": {str(k): v for k, v in self.node_to_container.items()},
            "time": time.time(),
        }

    def node_statuses(self) -> Dict:
        rows = self.monitor.measure_all(self.topology.nodes)
        return {"edge_id": self.cfg.edge_id, "statuses": rows, "time": time.time()}

    def resource_snapshot(self, samples: int = 1, gap_ms: int = 0, active_hold_ms: int = 1500) -> Dict:
        pairs = sorted(self.node_to_container.items(), key=lambda x: x[0])
        containers = [c for _, c in pairs]

        samples = max(int(samples), 1)
        gap_ms = max(int(gap_ms), 0)

        agg: Dict[str, Dict[str, float]] = {}
        stats_ok = False
        for i in range(samples):
            usage = docker_stats_bulk(containers)
            if usage:
                stats_ok = True
            for cname, u in usage.items():
                prev = agg.get(cname)
                if prev is None:
                    agg[cname] = dict(u)
                    continue
                prev["cpu_percent"] = max(float(prev.get("cpu_percent", 0.0)), float(u.get("cpu_percent", 0.0)))
                prev["mem_used_kib"] = max(float(prev.get("mem_used_kib", 0.0)), float(u.get("mem_used_kib", 0.0)))
                prev["mem_limit_kib"] = max(float(prev.get("mem_limit_kib", 1.0)), float(u.get("mem_limit_kib", 1.0)))
            if i + 1 < samples and gap_ms > 0:
                time.sleep(gap_ms / 1000.0)

        try:
            running_out = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True)
            running = {x.strip() for x in running_out.splitlines() if x.strip()}
        except Exception:
            running = set()
        rows = []
        with self._active_lock:
            active_snap = dict(self._active_by_node)
            last_active_snap = dict(self._last_active_ts_by_node)
        now_ts = time.time()
        hold_sec = max(float(active_hold_ms), 0.0) / 1000.0
        for nid, cname in pairs:
            u = agg.get(cname, {"cpu_percent": 0.0, "mem_used_kib": 0.0, "mem_limit_kib": 1.0})
            recent = 0
            if hold_sec > 0 and (now_ts - float(last_active_snap.get(nid, 0.0))) <= hold_sec:
                recent = 1
            rows.append(
                {
                    "local_node_id": nid,
                    "container": cname,
                    "cpu_percent": u["cpu_percent"],
                    "mem_used_kib": u["mem_used_kib"],
                    "mem_limit_kib": u["mem_limit_kib"],
                    "active_tasks": int(active_snap.get(nid, 0)),
                    "active_recent": recent,
                }
            )
        return {
            "edge_id": self.cfg.edge_id,
            "hostname": socket.gethostname(),
            "resources": rows,
            "stats_ok": stats_ok,
            "expected_container_count": len(containers),
            "running_container_count": len(running),
            "mapped_running_count": sum(1 for c in containers if c in running),
            "active_tasks_total": int(sum(active_snap.values())),
            "active_recent_total": int(sum(1 for nid in active_snap if (now_ts - float(last_active_snap.get(nid, 0.0))) <= hold_sec)),
            "samples": samples,
            "gap_ms": gap_ms,
            "active_hold_ms": int(max(active_hold_ms, 0)),
            "time": time.time(),
        }


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    runtime: Optional[EdgeRuntime] = None

    def _json(self, code: int, payload: Dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if Handler.runtime is None:
            self._json(500, {"ok": False, "error": "runtime not initialized"})
            return
        if route == "/health":
            self._json(200, {"ok": True, "time": time.time()})
            return
        if route == "/meta":
            self._json(200, {"ok": True, "data": Handler.runtime.meta()})
            return
        if route == "/node_statuses":
            self._json(200, {"ok": True, "data": Handler.runtime.node_statuses()})
            return
        if route == "/resource_snapshot":
            samples = int(query.get("samples", ["1"])[0])
            gap_ms = int(query.get("gap_ms", ["0"])[0])
            active_hold_ms = int(query.get("active_hold_ms", ["1500"])[0])
            self._json(
                200,
                {
                    "ok": True,
                    "data": Handler.runtime.resource_snapshot(
                        samples=samples,
                        gap_ms=gap_ms,
                        active_hold_ms=active_hold_ms,
                    ),
                },
            )
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if Handler.runtime is None:
            self._json(500, {"ok": False, "error": "runtime not initialized"})
            return
        if self.path != "/run_task":
            self._json(404, {"ok": False, "error": "not found"})
            return

        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(n) if n > 0 else b"{}"
            req = json.loads(body.decode("utf-8"))
            local_node_id = int(req.get("local_node_id"))
            task = dict(req.get("task", {}))
            timeout_sec = float(req.get("timeout_sec", Handler.runtime.cfg.task_timeout_sec))
        except Exception as e:
            self._json(400, {"ok": False, "error": f"bad request: {e}"})
            return

        result = Handler.runtime.run_real_task(local_node_id=local_node_id, task=task, timeout_sec=timeout_sec)
        self._json(200, {"ok": True, "data": result})

    def log_message(self, fmt, *args):
        return


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v4.0 edge agent (run on each Raspberry Pi)")
    p.add_argument("--edge-id", type=str, default="pi1")
    p.add_argument("--flag", type=str, default="25N50E")
    p.add_argument("--host", type=str, default="0.0.0.0")
    p.add_argument("--port", type=int, default=18080)
    p.add_argument("--topo-config-path", type=str, default="eval/benchmarks/Topo4MEC/data/25N50E/config.json")
    p.add_argument("--ingress-path", type=str, default="eval/benchmarks/Topo4MEC/source/25N50E/ingress.txt")
    p.add_argument("--runtime-map-path", type=str, default="RCS_Docker2.0/runtime_map_25n50e.json")
    p.add_argument("--task-timeout-sec", type=float, default=3.0)
    p.add_argument("--per-node-parallelism", type=int, default=1)
    p.add_argument("--real-task-profile", type=str, default="cpu-heavy")
    p.add_argument("--real-task-base-sec", type=float, default=1.8)
    p.add_argument("--real-task-payload-scale", type=float, default=1.0)
    p.add_argument("--real-task-max-ops", type=int, default=96)
    p.add_argument("--real-task-repeat", type=int, default=1)
    p.add_argument("--real-task-target-sec", type=float, default=0.0)
    return p.parse_args()


def main():
    args = parse_args()
    cfg = EdgeConfig(
        edge_id=args.edge_id,
        flag=args.flag,
        host=args.host,
        port=args.port,
        topo_config_path=resolve_input_path(args.topo_config_path),
        ingress_path=resolve_input_path(args.ingress_path),
        runtime_map_path=resolve_input_path(args.runtime_map_path),
        task_timeout_sec=args.task_timeout_sec,
        per_node_parallelism=args.per_node_parallelism,
        real_task_profile=args.real_task_profile,
        real_task_base_sec=args.real_task_base_sec,
        real_task_payload_scale=args.real_task_payload_scale,
        real_task_max_ops=args.real_task_max_ops,
        real_task_repeat=args.real_task_repeat,
        real_task_target_sec=args.real_task_target_sec,
    )
    runtime = EdgeRuntime(cfg)
    Handler.runtime = runtime

    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    print(f"[edge-agent] edge_id={cfg.edge_id} listening on {cfg.host}:{cfg.port}", flush=True)
    print(f"[edge-agent] runtime_map={cfg.runtime_map_path}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
