import argparse
import json
import os
from typing import Dict, List, Set


SUPPORTED_FLAGS = ("25N50E", "50N50E", "100N150E", "MilanCityCenter")
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_topo_config(flag: str) -> str:
    return os.path.join(ROOT_DIR, "eval", "benchmarks", "Topo4MEC", "data", flag, "config.json")


def _default_ingress_path(flag: str) -> str:
    return os.path.join(ROOT_DIR, "eval", "benchmarks", "Topo4MEC", "source", flag, "ingress.txt")


def _default_runtime_map(flag: str) -> str:
    return os.path.join(ROOT_DIR, "RCS_Docker", f"runtime_map_{flag.lower()}.json")


def _default_compose_out(flag: str) -> str:
    return os.path.join(ROOT_DIR, "RCS_Docker", f"docker-compose.{flag.lower()}.yml")


def _load_topology_nodes(path: str) -> Dict[int, Dict[str, float]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    node_map: Dict[int, Dict[str, float]] = {}
    for node in cfg["Nodes"]:
        node_id = int(node["NodeId"])
        node_map[node_id] = {
            "max_cpu_freq": float(node["MaxCpuFreq"]),
            "max_buffer_size": float(node["MaxBufferSize"]),
        }
    return node_map


def _load_ingress_nodes(path: str) -> Set[int]:
    if not os.path.exists(path):
        return set()

    with open(path, "r", encoding="utf-8") as f:
        lines = [x.strip() for x in f.readlines() if x.strip()]
    if len(lines) < 2:
        return set()

    return {int(x) - 1 for x in lines[1].split()}


def _load_or_create_runtime_map(
    runtime_map_path: str,
    node_ids: List[int],
    ingress_node_ids: Set[int],
    name_prefix: str,
) -> Dict:
    local_node_id = None
    ingress = sorted(list(ingress_node_ids))

    if os.path.exists(runtime_map_path):
        with open(runtime_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        local_node_id = data.get("local_node_id", None)
        ingress = data.get("ingress_node_ids", ingress)

    data = {
        "local_node_id": local_node_id,
        "ingress_node_ids": ingress,
        "node_to_container": {str(i): f"{name_prefix}node{i}" for i in node_ids},
    }
    os.makedirs(os.path.dirname(runtime_map_path), exist_ok=True)
    with open(runtime_map_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def _cpu_quota(node_cpu: float, max_cpu: float) -> float:
    return max(0.2, round((node_cpu / max(max_cpu, 1e-6)) * 2.0, 2))


def _mem_limit_mb(node_buffer: float, max_buffer: float) -> int:
    scaled = int((node_buffer / max(max_buffer, 1e-6)) * 768.0 + 256.0)
    return max(256, min(scaled, 2048))


def generate_compose(
    flag: str,
    topo_config: str,
    runtime_map_path: str,
    ingress_path: str,
    out_file: str,
    name_prefix: str,
) -> None:
    topo_nodes = _load_topology_nodes(topo_config)
    node_ids = sorted(topo_nodes.keys())
    ingress = _load_ingress_nodes(ingress_path)
    cfg = _load_or_create_runtime_map(runtime_map_path, node_ids, ingress, name_prefix)

    max_cpu = max(v["max_cpu_freq"] for v in topo_nodes.values())
    max_buffer = max(v["max_buffer_size"] for v in topo_nodes.values())

    services = []
    for node_key, container in sorted(cfg["node_to_container"].items(), key=lambda x: int(x[0])):
        node_id = int(node_key)
        if node_id not in topo_nodes:
            continue

        node = topo_nodes[node_id]
        cpus = _cpu_quota(node["max_cpu_freq"], max_cpu)
        mem_limit_mb = _mem_limit_mb(node["max_buffer_size"], max_buffer)
        ingress_flag = "true" if node_id in set(cfg.get("ingress_node_ids", [])) else "false"

        block = f"""  {container}:
    image: python:3.10-slim
    container_name: {container}
    command: sh -c \"python --version && tail -f /dev/null\"
    restart: unless-stopped
    cpus: {cpus}
    mem_limit: {mem_limit_mb}m
    environment:
      - DOCKER_SIM_NODE_ID={node_id}
      - DOCKER_SIM_INGRESS={ingress_flag}
    labels:
      docker_sim.node_id: \"{node_id}\"
      docker_sim.ingress: \"{ingress_flag}\"
"""
        services.append(block)

    text = "services:\n" + "\n".join(services)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"[OK] flag={flag}")
    print(f"Generated runtime map: {runtime_map_path}")
    print(f"Generated compose   : {out_file}")
    print(f"Name prefix         : {name_prefix}")
    print(f"Services            : {len(services)}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag", type=str, default="25N50E", choices=list(SUPPORTED_FLAGS))
    parser.add_argument("--topo-config", type=str, default="")
    parser.add_argument("--runtime-map", type=str, default="")
    parser.add_argument("--ingress-path", type=str, default="")
    parser.add_argument("--out-file", type=str, default="")
    parser.add_argument("--name-prefix", type=str, default="")
    return parser.parse_args()


def main():
    args = parse_args()
    flag = args.flag

    topo_config = args.topo_config or _default_topo_config(flag)
    runtime_map = args.runtime_map or _default_runtime_map(flag)
    ingress_path = args.ingress_path or _default_ingress_path(flag)
    out_file = args.out_file or _default_compose_out(flag)
    name_prefix = args.name_prefix if args.name_prefix else f"{flag.lower()}_"

    generate_compose(
        flag=flag,
        topo_config=topo_config,
        runtime_map_path=runtime_map,
        ingress_path=ingress_path,
        out_file=out_file,
        name_prefix=name_prefix,
    )


if __name__ == "__main__":
    main()
