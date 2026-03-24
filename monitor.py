import subprocess
from typing import Dict, List, Optional

import psutil

from topology import NodeSpec


class DockerNodeMonitor:
    """Collect runtime status for local host and docker containers."""

    def __init__(self, node_to_container: Dict[int, str], local_node_id: Optional[int] = 0):
        self.node_to_container = node_to_container
        self.local_node_id = local_node_id
        self.running_containers = self._get_running_containers()

    def _get_running_containers(self) -> set:
        try:
            cmd = "docker ps --format '{{.Names}}'"
            out = subprocess.check_output(cmd, shell=True, text=True)
            names = [x.strip() for x in out.splitlines() if x.strip()]
            return set(names)
        except Exception:
            return set()

    @staticmethod
    def _parse_memory_to_mb(mem_str: str) -> float:
        value = mem_str.strip()
        if "GiB" in value:
            return float(value.replace("GiB", "")) * 1024.0
        if "MiB" in value:
            return float(value.replace("MiB", ""))
        if "KiB" in value:
            return float(value.replace("KiB", "")) / 1024.0
        if "B" in value:
            return float(value.replace("B", "")) / (1024.0 * 1024.0)
        return 0.0

    def _docker_usage_bulk(self, container_names: List[str]) -> Dict[str, Dict[str, float]]:
        if not container_names:
            return {}

        joined = " ".join(container_names)
        cmd = (
            "docker stats "
            f"{joined} --no-stream --format "
            "'{{.Name}},{{.CPUPerc}},{{.MemPerc}},{{.MemUsage}}'"
        )
        out = subprocess.check_output(cmd, shell=True, text=True).strip()
        rows = [x.strip() for x in out.splitlines() if x.strip()]

        parsed: Dict[str, Dict[str, float]] = {}
        for row in rows:
            parts = [x.strip() for x in row.split(",", 3)]
            if len(parts) != 4:
                continue
            name, cpu_s, mem_s, mem_usage = parts
            cpu_usage = float(cpu_s.replace("%", "")) / 100.0
            mem_usage_ratio = float(mem_s.replace("%", "")) / 100.0
            mem_now, mem_total = [x.strip() for x in mem_usage.split("/")]
            mem_now_mb = self._parse_memory_to_mb(mem_now)
            mem_total_mb = max(self._parse_memory_to_mb(mem_total), 1e-6)
            parsed[name] = {
                "cpu_usage": cpu_usage,
                "mem_usage_ratio": mem_usage_ratio,
                "queue_ratio": min(mem_now_mb / mem_total_mb, 1.0),
            }
        return parsed

    @staticmethod
    def _local_usage() -> Dict[str, float]:
        return {
            "cpu_usage": psutil.cpu_percent(interval=0.1) / 100.0,
            "mem_usage_ratio": psutil.virtual_memory().percent / 100.0,
        }

    def measure_node(
        self,
        node: NodeSpec,
        running_containers: Optional[set] = None,
        usage_map: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict[str, float]:
        running = self.running_containers if running_containers is None else running_containers
        usage_map = usage_map or {}

        if self.local_node_id is not None and node.node_id == self.local_node_id:
            local = self._local_usage()
            free_cpu = max(node.max_cpu_freq * (1.0 - local["cpu_usage"]), 0.0)
            free_buf = max(node.max_buffer_size * (1.0 - local["mem_usage_ratio"]), 0.0)
            return {
                "node_id": node.node_id,
                "free_cpu_freq": free_cpu,
                "free_buffer_size": free_buf,
                "idle_energy_coef": node.idle_energy_coef,
                "exe_energy_coef": node.exe_energy_coef,
                "cpu_usage": local["cpu_usage"],
                "queue_ratio": local["mem_usage_ratio"],
            }

        container_name = self.node_to_container.get(node.node_id)
        if not container_name or container_name not in running:
            return {
                "node_id": node.node_id,
                "free_cpu_freq": node.max_cpu_freq,
                "free_buffer_size": node.max_buffer_size,
                "idle_energy_coef": node.idle_energy_coef,
                "exe_energy_coef": node.exe_energy_coef,
                "cpu_usage": 0.0,
                "queue_ratio": 0.0,
            }

        usage = usage_map.get(container_name)
        if usage is None:
            usage = {"cpu_usage": 0.0, "queue_ratio": 0.0}

        free_cpu = max(node.max_cpu_freq * (1.0 - usage["cpu_usage"]), 0.0)
        free_buf = max(node.max_buffer_size * (1.0 - usage["queue_ratio"]), 0.0)
        return {
            "node_id": node.node_id,
            "free_cpu_freq": free_cpu,
            "free_buffer_size": free_buf,
            "idle_energy_coef": node.idle_energy_coef,
            "exe_energy_coef": node.exe_energy_coef,
            "cpu_usage": usage["cpu_usage"],
            "queue_ratio": usage["queue_ratio"],
        }

    def measure_all(self, nodes: Dict[int, NodeSpec]) -> List[Dict[str, float]]:
        self.running_containers = self._get_running_containers()
        tracked = [c for c in self.node_to_container.values() if c in self.running_containers]
        usage_map: Dict[str, Dict[str, float]] = {}
        if tracked:
            try:
                usage_map = self._docker_usage_bulk(tracked)
            except Exception:
                usage_map = {}

        return [self.measure_node(nodes[i], self.running_containers, usage_map) for i in range(len(nodes))]
