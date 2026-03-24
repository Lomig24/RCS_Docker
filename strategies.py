import random
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from topology import TopologyTopo4MEC


class StrategyBase(ABC):
    name = "base"

    @abstractmethod
    def select_node(
        self,
        task: Dict[str, float],
        src_node_id: int,
        node_statuses: list,
        model_input: Optional[object],
        topology: TopologyTopo4MEC,
    ) -> Tuple[int, Dict[str, float]]:
        raise NotImplementedError


class RandomStrategy(StrategyBase):
    name = "Random"

    def __init__(self, n_nodes: int):
        self.n_nodes = n_nodes

    def select_node(self, task, src_node_id, node_statuses, model_input, topology):
        return random.randint(0, self.n_nodes - 1), {}


class RoundRobinStrategy(StrategyBase):
    name = "RoundRobin"

    def __init__(self, n_nodes: int):
        self.n_nodes = n_nodes
        self.cursor = -1

    def select_node(self, task, src_node_id, node_statuses, model_input, topology):
        self.cursor = (self.cursor + 1) % self.n_nodes
        return self.cursor, {}


class GreedyLatencyStrategy(StrategyBase):
    name = "GreedyLatency"

    def __init__(self, n_nodes: int):
        self.n_nodes = n_nodes

    def select_node(self, task, src_node_id, node_statuses, model_input, topology):
        best_node = 0
        best_latency = float("inf")

        transmission_time = task["task_size"] / max(task["trans_bit_rate"], 1e-6)
        task_units = task["task_size"] * task["cycles_per_bit"]

        for node_id in range(self.n_nodes):
            cpu_speed = max(node_statuses[node_id]["free_cpu_freq"], 0.0)
            computation_time = task_units / (cpu_speed + 1.0)
            total_time = transmission_time + computation_time
            if total_time < best_latency:
                best_latency = total_time
                best_node = node_id

        return best_node, {"greedy_pred_latency": float(best_latency)}
