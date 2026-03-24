import json
import os
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np


@dataclass
class NodeSpec:
    node_id: int
    node_name: str
    max_cpu_freq: float
    max_buffer_size: float
    idle_energy_coef: float
    exe_energy_coef: float


class TopologyTopo4MEC:
    """Load Topo4MEC topology and provide hop/transfer estimation."""

    def __init__(self, config_path: str, ingress_path: Optional[str] = None):
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.nodes: Dict[int, NodeSpec] = {}
        for node in cfg["Nodes"]:
            spec = NodeSpec(
                node_id=int(node["NodeId"]),
                node_name=str(node["NodeName"]),
                max_cpu_freq=float(node["MaxCpuFreq"]),
                max_buffer_size=float(node["MaxBufferSize"]),
                idle_energy_coef=float(node["IdleEnergyCoef"]),
                exe_energy_coef=float(node["ExeEnergyCoef"]),
            )
            self.nodes[spec.node_id] = spec

        self.adj: Dict[int, List[int]] = {i: [] for i in self.nodes}
        self.bandwidth: Dict[Tuple[int, int], float] = {}
        for edge in cfg["Edges"]:
            src = int(edge["SrcNodeID"])
            dst = int(edge["DstNodeID"])
            bw = float(edge["Bandwidth"])
            self.adj[src].append(dst)
            self.adj[dst].append(src)
            self.bandwidth[(src, dst)] = bw
            self.bandwidth[(dst, src)] = bw

        self.n_nodes = len(self.nodes)
        self.hops = self._build_hop_matrix()
        self.path_min_bw = self._build_path_min_bw_matrix()
        self.ingress_nodes = self._load_ingress_nodes(ingress_path)

    def _load_ingress_nodes(self, ingress_path: Optional[str]) -> Set[int]:
        if ingress_path is None:
            scenario_flag = os.path.basename(os.path.dirname(os.path.abspath(self.config_path)))
            ingress_path = os.path.normpath(
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..",
                    "eval",
                    "benchmarks",
                    "Topo4MEC",
                    "source",
                    scenario_flag,
                    "ingress.txt",
                )
            )
        if not os.path.exists(ingress_path):
            return set()

        with open(ingress_path, "r", encoding="utf-8") as f:
            lines = [x.strip() for x in f.readlines() if x.strip()]
        if len(lines) < 2:
            return set()

        return {int(x) - 1 for x in lines[1].split()}

    def _build_hop_matrix(self) -> np.ndarray:
        hops = np.zeros((self.n_nodes, self.n_nodes), dtype=np.float32)
        for src in range(self.n_nodes):
            for dst in range(self.n_nodes):
                if src != dst:
                    hops[src, dst] = self._shortest_hops_bfs(src, dst)
        return hops

    def _build_path_min_bw_matrix(self) -> np.ndarray:
        bw = np.full((self.n_nodes, self.n_nodes), 1e9, dtype=np.float32)
        for src in range(self.n_nodes):
            for dst in range(self.n_nodes):
                if src == dst:
                    continue
                path = self.shortest_path(src, dst)
                bw[src, dst] = float(self.path_min_bandwidth(path))
        return bw

    def _shortest_hops_bfs(self, src: int, dst: int) -> int:
        if src == dst:
            return 0
        seen = {src}
        queue = deque([(src, 0)])
        while queue:
            node, d = queue.popleft()
            for nxt in self.adj[node]:
                if nxt in seen:
                    continue
                if nxt == dst:
                    return d + 1
                seen.add(nxt)
                queue.append((nxt, d + 1))
        raise ValueError(f"No path between nodes: {src} -> {dst}")

    def name_to_id(self, node_name: str) -> int:
        if node_name.startswith("n"):
            return int(node_name[1:])
        return int(node_name)

    def shortest_path(self, src_id: int, dst_id: int) -> List[int]:
        if src_id == dst_id:
            return [src_id]

        parent = {src_id: -1}
        queue = deque([src_id])
        found = False
        while queue and not found:
            cur = queue.popleft()
            for nxt in self.adj[cur]:
                if nxt in parent:
                    continue
                parent[nxt] = cur
                if nxt == dst_id:
                    found = True
                    break
                queue.append(nxt)

        if dst_id not in parent:
            raise ValueError(f"No path between nodes: {src_id} -> {dst_id}")

        path = [dst_id]
        while path[-1] != src_id:
            path.append(parent[path[-1]])
        path.reverse()
        return path

    def path_min_bandwidth(self, path: List[int]) -> float:
        if len(path) <= 1:
            return 1e9
        bws: List[float] = []
        for i in range(len(path) - 1):
            bws.append(self.bandwidth[(path[i], path[i + 1])])
        return min(bws)

    def estimate_transfer_time(self, task_size: float, trans_bit_rate: float, src_id: int, dst_id: int) -> float:
        if src_id == dst_id:
            return 0.0

        hops = max(int(self.hops[src_id, dst_id]), 1)
        path_bw = max(float(self.path_min_bw[src_id, dst_id]), 1e-6)
        effective_rate = max(min(trans_bit_rate, path_bw), 1e-6)
        ingress_penalty = 0.02 if src_id in self.ingress_nodes else 0.0
        return float((task_size / effective_rate) * hops + ingress_penalty)
