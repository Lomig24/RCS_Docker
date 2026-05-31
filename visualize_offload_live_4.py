import argparse
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime
from typing import Dict, List
import time

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# Default pie colors: edit these directly in code to force specific colors
DEFAULT_PIE_COLORS = ["#fa7f6f", "#7174e3","#8ecfc9","#82b0d2","#ffbe7a","#beb8dc"]


def parse_args():
    p = argparse.ArgumentParser(description="v4.0 live monitor (host + 3 edges)")
    p.add_argument("--config", type=str, default="RCS_Docker2.0/host_config_example.json")
    p.add_argument("--trace-path", type=str, default="RCS_Docker2.0/outputs/2.0/trace/offload_trace_v20.jsonl")
    p.add_argument("--refresh-sec", type=float, default=1.0)
    p.add_argument("--cpu-ymax-percent", type=float, default=0.0)
    p.add_argument("--mem-ymax-kib", type=float, default=0.0)
    p.add_argument("--resource-samples", type=int, default=2, help="per-refresh sampling bursts on edge side")
    p.add_argument("--resource-gap-ms", type=int, default=40, help="gap between burst samples in ms")
    p.add_argument("--edge-timeout-sec", type=float, default=8.0, help="HTTP timeout when polling each edge")
    p.add_argument("--parallel-poll", action=argparse.BooleanOptionalAction, default=True, help="poll all edges in parallel")
    p.add_argument("--cpu-hold-sec", type=float, default=3.0, help="peak-hold window for CPU display")
    p.add_argument("--active-hold-ms", type=int, default=2000, help="keep node active marker for this duration after task finish")
    p.add_argument("--mem-mode", type=str, default="delta", choices=["raw", "delta"], help="raw: absolute usage, delta: usage minus baseline")
    p.add_argument("--count-strategy", type=str, default="ModelBased", help="task count filter by strategy; use ALL for no filter")
    p.add_argument("--count-window-lines", type=int, default=3000)
    p.add_argument("--group-size", type=int, default=5, help="number of contiguous bars sharing the same color")
    p.add_argument(
        "--palette",
        type=str,
        default="",
        help="comma-separated hex colors used cyclically for grouped bars (leave empty to use DEFAULT_PIE_COLORS in code)",
    )
    return p.parse_args()


def resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    cands = [
        os.path.join(os.getcwd(), path),
        os.path.join(root, path),
        os.path.join(here, path),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return os.path.join(root, path)


def http_get_json(url: str, timeout: float = 4.0) -> Dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_edges(cfg_path: str) -> List[Dict]:
    with open(cfg_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("edges", [])


def tail_trace_counts(trace_path: str, max_lines: int = 3000, strategy_filter: str = "ModelBased") -> Counter:
    if not os.path.exists(trace_path):
        return Counter()
    with open(trace_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    lines = lines[-max_lines:]
    cnt = Counter()
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            evt = json.loads(s)
        except Exception:
            continue
        st = str(evt.get("strategy", "")).strip()
        if strategy_filter.upper() != "ALL" and st != strategy_filter:
            continue
        edge = evt.get("selected_edge")
        if edge:
            cnt[str(edge)] += 1
    return cnt


def tail_latest_strategy(trace_path: str, max_lines: int = 2000) -> str:
    # Return the most-recent `strategy` value found in the tail of the trace file.
    if not os.path.exists(trace_path):
        return ""
    try:
        with open(trace_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""
    lines = lines[-max_lines:]
    for line in reversed(lines):
        s = line.strip()
        if not s:
            continue
        try:
            evt = json.loads(s)
        except Exception:
            continue
        st = str(evt.get("strategy", "")).strip()
        if st:
            return st
    return ""


class LiveState:
    def __init__(
        self,
        edges: List[Dict],
        trace_path: str,
        resource_samples: int = 1,
        resource_gap_ms: int = 0,
        mem_mode: str = "raw",
        edge_timeout_sec: float = 12.0,
        parallel_poll: bool = True,
        cpu_hold_sec: float = 3.0,
        active_hold_ms: int = 2000,
    ):
        self.edges = edges
        self.trace_path = trace_path
        self.resource_samples = max(int(resource_samples), 1)
        self.resource_gap_ms = max(int(resource_gap_ms), 0)
        self.edge_timeout_sec = max(float(edge_timeout_sec), 1.0)
        self.parallel_poll = bool(parallel_poll)
        self.cpu_hold_sec = max(float(cpu_hold_sec), 0.0)
        self.active_hold_ms = max(int(active_hold_ms), 0)
        self.mem_mode = mem_mode
        self.rows: List[Dict] = []
        self.error = ""
        self.ts = ""
        self.edge_stats_ok: Dict[str, bool] = {}
        self.last_rows_by_edge: Dict[str, List[Dict]] = {}
        self.mem_baseline: Dict[str, float] = {}
        self.edge_active_total: Dict[str, int] = {}
        self.cpu_hist: Dict[str, List] = {}

    def _pull_edge_rows(self, edge: Dict) -> Dict:
        edge_id = edge["edge_id"]
        url = (
            edge["base_url"].rstrip("/")
            + f"/resource_snapshot?samples={self.resource_samples}&gap_ms={self.resource_gap_ms}&active_hold_ms={self.active_hold_ms}"
        )
        resp = http_get_json(url, timeout=self.edge_timeout_sec)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "edge not ok"))

        data = resp.get("data", {})
        stats_ok = bool(data.get("stats_ok", True))
        active_total = int(data.get("active_tasks_total", 0))
        active_recent_total = int(data.get("active_recent_total", 0))
        rows: List[Dict] = []
        for r in data.get("resources", []):
            row = dict(r)
            row["edge_id"] = edge_id
            key = f"{edge_id}:{int(row.get('local_node_id', -1))}"
            now_ts = time.time()
            cpu_now = float(row.get("cpu_percent", 0.0))
            h = self.cpu_hist.setdefault(key, [])
            h.append((now_ts, cpu_now))
            hold_sec = self.cpu_hold_sec
            if hold_sec > 0:
                cutoff = now_ts - hold_sec
                h[:] = [x for x in h if x[0] >= cutoff]
                row["cpu_display"] = max((v for _, v in h), default=cpu_now)
            else:
                row["cpu_display"] = cpu_now
            mem_used = float(row.get("mem_used_kib", 0.0))
            if key not in self.mem_baseline:
                self.mem_baseline[key] = mem_used
            if self.mem_mode == "delta":
                row["mem_display_kib"] = max(mem_used - self.mem_baseline[key], 0.0)
            else:
                row["mem_display_kib"] = mem_used
            rows.append(row)
        return {
            "edge_id": edge_id,
            "rows": rows,
            "stats_ok": stats_ok,
            "active_total": active_total,
            "active_recent_total": active_recent_total,
        }

    def refresh(self):
        all_rows: List[Dict] = []
        errors = []

        if self.parallel_poll and len(self.edges) > 1:
            with ThreadPoolExecutor(max_workers=len(self.edges)) as ex:
                fut_map = {ex.submit(self._pull_edge_rows, edge): edge["edge_id"] for edge in self.edges}
                for fut in as_completed(fut_map):
                    edge_id = fut_map[fut]
                    try:
                        res = fut.result()
                        self.edge_stats_ok[edge_id] = bool(res.get("stats_ok", False))
                        self.edge_active_total[edge_id] = int(res.get("active_recent_total", res.get("active_total", 0)))
                        self.last_rows_by_edge[edge_id] = list(res.get("rows", []))
                        all_rows.extend(res.get("rows", []))
                    except Exception as e:
                        self.edge_stats_ok[edge_id] = False
                        errors.append(f"{edge_id}:{e}")
                        if edge_id in self.last_rows_by_edge:
                            stale_rows = []
                            for row in self.last_rows_by_edge[edge_id]:
                                stale_row = dict(row)
                                stale_row["stale"] = True
                                stale_rows.append(stale_row)
                            all_rows.extend(stale_rows)
        else:
            for edge in self.edges:
                edge_id = edge["edge_id"]
                try:
                    res = self._pull_edge_rows(edge)
                    self.edge_stats_ok[edge_id] = bool(res.get("stats_ok", False))
                    self.edge_active_total[edge_id] = int(res.get("active_recent_total", res.get("active_total", 0)))
                    self.last_rows_by_edge[edge_id] = list(res.get("rows", []))
                    all_rows.extend(res.get("rows", []))
                except Exception as e:
                    self.edge_stats_ok[edge_id] = False
                    errors.append(f"{edge_id}:{e}")
                    if edge_id in self.last_rows_by_edge:
                        stale_rows = []
                        for row in self.last_rows_by_edge[edge_id]:
                            stale_row = dict(row)
                            stale_row["stale"] = True
                            stale_rows.append(stale_row)
                        all_rows.extend(stale_rows)
        self.rows = sorted(all_rows, key=lambda x: (x["edge_id"], x["local_node_id"]))
        self.error = "; ".join(errors)
        self.ts = datetime.now().strftime("%H:%M:%S")


def main():
    args = parse_args()
    cfg_path = resolve_path(args.config)
    trace_path = resolve_path(args.trace_path)
    edges = load_edges(cfg_path)

    state = LiveState(
        edges=edges,
        trace_path=trace_path,
        resource_samples=args.resource_samples,
        resource_gap_ms=args.resource_gap_ms,
        mem_mode=args.mem_mode,
        edge_timeout_sec=args.edge_timeout_sec,
        parallel_poll=args.parallel_poll,
        cpu_hold_sec=args.cpu_hold_sec,
        active_hold_ms=args.active_hold_ms,
    )

    fig, (ax_cpu, ax_mem, ax_cnt) = plt.subplots(
        3, 1, figsize=(16, 12), constrained_layout=True, gridspec_kw={"height_ratios": [2, 2, 1.6]}
    )

    def render(_):
        state.refresh()
        # detect most-recent strategy in the trace and prefer it (so the pie follows the running method)
        latest_strat = tail_latest_strategy(trace_path, max_lines=max(200, args.count_window_lines))
        use_strategy = latest_strat or args.count_strategy
        counts = tail_trace_counts(
            trace_path,
            max_lines=max(args.count_window_lines, 100),
            strategy_filter=use_strategy,
        )

        labels = [f"{r['edge_id']}:n{r['local_node_id']}" for r in state.rows]
        cpu_vals = [float(r.get("cpu_display", r.get("cpu_percent", 0.0))) for r in state.rows]
        mem_vals = [float(r.get("mem_display_kib", r.get("mem_used_kib", 0.0))) for r in state.rows]
        # in-flight active values were used previously; we keep active totals in the title
        # active_vals = [float(max(r.get("active_tasks", 0.0), r.get("active_recent", 0.0))) for r in state.rows]
        stale_flags = [bool(r.get("stale", False)) for r in state.rows]

        x = list(range(len(labels)))

        ax_cpu.clear()
        # build palette: prefer explicit CLI palette if provided, otherwise use DEFAULT_PIE_COLORS
        if args.palette and args.palette.strip():
            palette = [c.strip() for c in args.palette.split(",") if c.strip()]
        else:
            palette = list(DEFAULT_PIE_COLORS)
        group = max(1, int(args.group_size))
        cpu_colors = []
        for i, stale in enumerate(stale_flags):
            if stale:
                cpu_colors.append("#9aa0a6")
            else:
                idx = (i // group) % max(1, len(palette))
                cpu_colors.append(palette[idx])
        ax_cpu.bar(x, cpu_vals, color=cpu_colors, alpha=0.95)
        ax_cpu.set_xticks(x)
        ax_cpu.set_xticklabels(labels, rotation=90, fontsize=7)
        ymax_cpu = max(max(cpu_vals) * 1.35 if cpu_vals else 2.0, 2.0)
        if args.cpu_ymax_percent > 0:
            ymax_cpu = max(args.cpu_ymax_percent, 1.0)
        ax_cpu.set_ylim(0, ymax_cpu)
        ax_cpu.set_title(f"v4.0 CPU usage by edge node (docker stats %, peak-hold={args.cpu_hold_sec:.1f}s)")
        ax_cpu.set_ylabel("CPU %")
        ax_cpu.grid(True, alpha=0.3)

        ax_mem.clear()
        mem_colors = []
        for i, stale in enumerate(stale_flags):
            if stale:
                mem_colors.append("#9aa0a6")
            else:
                idx = (i // group) % max(1, len(palette))
                mem_colors.append(palette[idx])
        ax_mem.bar(x, mem_vals, color=mem_colors, alpha=0.95)
        ax_mem.set_xticks(x)
        ax_mem.set_xticklabels(labels, rotation=90, fontsize=7)
        ymax_mem = max(max(mem_vals) * 1.2 if mem_vals else 1000.0, 1000.0)
        if args.mem_ymax_kib > 0:
            ymax_mem = max(args.mem_ymax_kib, 1000.0)
        ax_mem.set_ylim(0, ymax_mem)
        ax_mem.set_title(f"v4.0 memory usage by edge node (KiB, mode={args.mem_mode})")
        ax_mem.set_ylabel("Mem (KiB)")
        ax_mem.grid(True, alpha=0.3)

        # Replace in-flight bar with a pie chart for task distribution across edges
        ax_cnt.clear()
        edge_labels = [e["edge_id"] for e in edges]
        edge_vals = [counts.get(eid, 0) for eid in edge_labels]
        # ensure palette for pie: use first colors (or cycle)
        pie_colors = []
        for i in range(len(edge_labels)):
            pie_colors.append(palette[i % max(1, len(palette))])
        # draw pie chart centered in the axis; place labels in a legend to avoid overlap
        total = sum(edge_vals)
        if total == 0:
            ax_cnt.pie([1], labels=None, colors=["#d3d3d3"], startangle=90)
            ax_cnt.text(0, 0, "no recent tasks", ha="center", va="center", fontsize=12)
        else:
            # show absolute count + percent inside slices, keep text compact
            wedges, texts, autotexts = ax_cnt.pie(
                edge_vals,
                labels=None,
                autopct=lambda pct: f"{int(round(pct * total / 100.0))}\n{pct:.0f}%",
                colors=pie_colors,
                startangle=90,
                pctdistance=0.65,
                textprops={"fontsize": 9},
            )
            # add a legend on the right listing edge label and absolute count
            legend_labels = [f"{lab}: {edge_vals[i]}" for i, lab in enumerate(edge_labels)]
            # place legend horizontally at the bottom to avoid reserving right-side space
            ax_cnt.legend(
                wedges,
                legend_labels,
                # title="Edges",
                loc="lower center",
                bbox_to_anchor=(0.5, -0.18),
                ncol=max(1, len(edge_labels)),
                frameon=False,
            )
        ax_cnt.axis("equal")
        ax_cnt.set_title(f"Recent offload distribution by edge (strategy={use_strategy})")

        title = (
            f"sampled_at={state.ts} | total_nodes={len(state.rows)}"
            f" | samples={state.resource_samples} gap_ms={state.resource_gap_ms}"
            f" timeout={state.edge_timeout_sec:.1f}s"
        )
        if state.edge_active_total:
            active_txt = ",".join([f"{k}:{v}" for k, v in sorted(state.edge_active_total.items())])
            title += f" | edge_active={active_txt}"
        bad_edges = [eid for eid, ok in state.edge_stats_ok.items() if not ok]
        if bad_edges:
            title += f" | stats_unavailable={','.join(bad_edges)}"
        if state.error:
            title += f" | error={state.error}"
        fig.suptitle(title)

    interval_ms = max(int(args.refresh_sec * 1000), 200)
    # leave room at the bottom for a horizontal legend
    fig.subplots_adjust(bottom=0.14)
    anim = FuncAnimation(fig, render, interval=interval_ms, cache_frame_data=False)
    _ = anim
    plt.show()


if __name__ == "__main__":
    main()
