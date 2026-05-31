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


class Topology25N50E:
    def __init__(self, config_path: str, ingress_path: Optional[str] = None):
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.nodes: Dict[int, NodeSpec] = {}
        for n in cfg["Nodes"]:
            spec = NodeSpec(
                node_id=int(n["NodeId"]),
                node_name=str(n["NodeName"]),
                max_cpu_freq=float(n["MaxCpuFreq"]),
                max_buffer_size=float(n["MaxBufferSize"]),
                idle_energy_coef=float(n["IdleEnergyCoef"]),
                exe_energy_coef=float(n["ExeEnergyCoef"]),
            )
            self.nodes[spec.node_id] = spec

        self.adj: Dict[int, List[int]] = {i: [] for i in self.nodes}
        self.bandwidth: Dict[Tuple[int, int], float] = {}
        for e in cfg["Edges"]:
            src = int(e["SrcNodeID"])
            dst = int(e["DstNodeID"])
            bw = float(e["Bandwidth"])
            self.adj[src].append(dst)
            self.adj[dst].append(src)
            self.bandwidth[(src, dst)] = bw
            self.bandwidth[(dst, src)] = bw

        self.n_nodes = len(self.nodes)
        self.hops = self._build_hop_matrix()
        self.path_min_bw = self._build_path_min_bw_matrix()
        self.hops_norm = self._normalize_hops(self.hops)
        self.node_attr_max = self._build_node_attr_max()
        self.ingress_nodes = self._load_ingress_nodes(ingress_path)

    def _build_hop_matrix(self) -> np.ndarray:
        hops = np.zeros((self.n_nodes, self.n_nodes), dtype=np.float32)
        for i in range(self.n_nodes):
            for j in range(self.n_nodes):
                if i == j:
                    continue
                hops[i, j] = self._shortest_hops_bfs(i, j)
        return hops

    def _build_path_min_bw_matrix(self) -> np.ndarray:
        bw = np.full((self.n_nodes, self.n_nodes), 1e9, dtype=np.float32)
        for i in range(self.n_nodes):
            for j in range(self.n_nodes):
                if i == j:
                    continue
                path = self.shortest_path(i, j)
                bw[i, j] = float(self.path_min_bandwidth(path))
        return bw

    def _load_ingress_nodes(self, ingress_path: Optional[str]) -> Set[int]:
        if ingress_path is None:
            return set()
        if not os.path.exists(ingress_path):
            return set()
        with open(ingress_path, "r", encoding="utf-8") as f:
            lines = [x.strip() for x in f.readlines() if x.strip()]
        if len(lines) < 2:
            return set()
        return {int(x) - 1 for x in lines[1].split()}

    def _shortest_hops_bfs(self, src: int, dst: int) -> int:
        if src == dst:
            return 0
        seen = {src}
        q = deque([(src, 0)])
        while q:
            node, d = q.popleft()
            for nxt in self.adj[node]:
                if nxt in seen:
                    continue
                if nxt == dst:
                    return d + 1
                seen.add(nxt)
                q.append((nxt, d + 1))
        raise ValueError(f"No path between nodes: {src} -> {dst}")

    @staticmethod
    def _normalize_hops(hops: np.ndarray) -> np.ndarray:
        max_hop = float(np.max(hops))
        if max_hop <= 0:
            return hops.copy()
        return (hops / max_hop).astype(np.float32)

    def _build_node_attr_max(self) -> np.ndarray:
        max_cpu = max(v.max_cpu_freq for v in self.nodes.values())
        max_buf = max(v.max_buffer_size for v in self.nodes.values())
        max_idle = max(v.idle_energy_coef for v in self.nodes.values())
        max_exe = max(v.exe_energy_coef for v in self.nodes.values())
        return np.array([max_cpu, max_buf, max_idle, max_exe], dtype=np.float32)

    def shortest_path(self, src_id: int, dst_id: int) -> List[int]:
        if src_id == dst_id:
            return [src_id]
        parent = {src_id: -1}
        q = deque([src_id])
        found = False
        while q and not found:
            cur = q.popleft()
            for nxt in self.adj[cur]:
                if nxt in parent:
                    continue
                parent[nxt] = cur
                if nxt == dst_id:
                    found = True
                    break
                q.append(nxt)

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

    def id_to_name(self, node_id: int) -> str:
        return self.nodes[node_id].node_name
