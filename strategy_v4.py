from typing import Dict, Optional, Tuple

import numpy as np
import torch

from policies.baselines.aux_sa.net_sa import PNet as AuxSAPNet
from policies.baselines.neo.net import PNet as NeoPNet

from topology_v4 import Topology25N50E


class ModelStrategy:
    name = "ModelBased"

    def __init__(
        self,
        n_nodes: int,
        model_path: Optional[str] = None,
        d_model: int = 256,
        d_hidden: int = 512,
        model_kind: str = "auto",
    ):
        kind = str(model_kind or "auto").strip().lower()
        if kind == "auto":
            p = str(model_path or "").lower()
            if "aux_sa" in p:
                kind = "aux_sa"
            elif "dqn" in p or "meta" in p or "neo" in p:
                kind = "neo"
            else:
                kind = "aux_sa"

        self.model_kind = kind
        if self.model_kind == "neo":
            self.model = NeoPNet(n=n_nodes, d_model=d_model, d_hidden=d_hidden)
        else:
            self.model = AuxSAPNet(n=n_nodes, d_model=d_model, d_hidden=d_hidden)

        self.model_loaded = False
        if model_path:
            try:
                ckp = torch.load(model_path, map_location="cpu")
                self.model.load_state_dict(ckp["model_state_dict"])
                self.model_loaded = True
            except Exception as e:
                print(f"[ModelStrategy] checkpoint load failed, fallback to random weights: {e}")
        self.model.eval()

    def select_node(
        self,
        task: Dict[str, float],
        src_node_id: int,
        node_statuses: list,
        model_input: Optional[np.ndarray],
        topology: Topology25N50E,
    ) -> Tuple[int, Dict[str, float]]:
        if model_input is None:
            raise ValueError("ModelStrategy requires model_input")

        x = torch.from_numpy(model_input).float()
        with torch.no_grad():
            out = self.model(x)
            if isinstance(out, tuple):
                q_values = out[0]
            else:
                q_values = out
            selected = int(q_values.argmax(dim=1).item())
        return selected, {"q_values": q_values.squeeze(0).squeeze(-1).numpy().tolist()}
