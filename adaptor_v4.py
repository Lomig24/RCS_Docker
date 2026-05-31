from typing import Dict, List

import numpy as np


class ModelRealAdaptorV2:
    TASK_ATTR_SIZE = 4
    NODE_ATTR_SIZE = 4

    def __init__(self, n_nodes: int, normalized_distance_matrix: np.ndarray, node_attr_max: np.ndarray):
        self.n_nodes = n_nodes
        self.distance = normalized_distance_matrix.astype(np.float32)
        self.node_attr_max = np.maximum(node_attr_max.astype(np.float32), 1e-6)

    def normalize_task_features(self, task: Dict[str, float]) -> np.ndarray:
        return np.array(
            [
                task["task_size"] / 100.0,
                task["cycles_per_bit"] / 10.0,
                task["trans_bit_rate"] / 50.0,
                task["ddl"] / 100.0,
            ],
            dtype=np.float32,
        )

    def normalize_node_features(self, node_status: Dict[str, float]) -> np.ndarray:
        raw = np.array(
            [
                node_status["free_cpu_freq"],
                node_status["free_buffer_size"],
                node_status["idle_energy_coef"],
                node_status["exe_energy_coef"],
            ],
            dtype=np.float32,
        )
        return raw / self.node_attr_max

    def build_model_input(self, task: Dict[str, float], task_src_node_id: int, all_node_statuses: List[Dict[str, float]]) -> np.ndarray:
        n = self.n_nodes
        node_features = np.zeros((n, self.NODE_ATTR_SIZE), dtype=np.float32)
        for i in range(n):
            node_features[i] = self.normalize_node_features(all_node_statuses[i])

        task_features = self.normalize_task_features(task)
        task_position = np.zeros(n, dtype=np.float32)
        if 0 <= task_src_node_id < n:
            task_position[task_src_node_id] = 1.0

        combined = np.concatenate(
            [
                self.distance.flatten(),
                node_features.flatten(),
                task_features,
                task_position,
            ]
        )
        return combined[np.newaxis, :]
