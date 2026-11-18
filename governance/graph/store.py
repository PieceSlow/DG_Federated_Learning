"""图谱存储：节点表 + 每种关系一张边表 + manifest + 版本历史。

两种写入模式：

* ``full``：整图重建（默认，保证同一输入得到同一结果）；
* ``incremental``：按主键 upsert，保留未出现的旧节点/边，记录差量。

边当表存的直接好处：可以直接 join/查询、加属性、增量追加，不必部署图数据库。
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from governance.graph.build import GraphData

NODE_KEYS = ["object_id"]
EDGE_KEYS = ["src_id", "dst_id", "relation"]


def _compare(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, dict]:
    """按主键对比并合并；返回 (合并后, 差量)。"""
    if old.empty:
        merged = new.drop_duplicates(keys).reset_index(drop=True)
        return merged, {"added": len(merged), "updated": 0, "unchanged": 0, "removed": 0}
    if new.empty:
        return old.reset_index(drop=True), {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}

    # 主键重复时 set_index 会让 loc[common] 展开出行数 > len(common)，delta 会算出负的
    # "updated"；这里先按主键去重（保留最后一条），差量才有意义。
    old = old.drop_duplicates(keys, keep="last")
    new = new.drop_duplicates(keys, keep="last")
    old_idx = old.set_index(keys)
    new_idx = new.set_index(keys)
    added = len(new_idx.index.difference(old_idx.index))
    common = new_idx.index.intersection(old_idx.index)
    unchanged = 0
    updated = 0
    if len(common):
        old_common = old_idx.loc[common].astype(str)
        new_common = new_idx.loc[common].astype(str)
        same = (old_common.values == new_common.values).all(axis=1)
        unchanged = int(same.sum())
        updated = int(len(common) - unchanged)
    merged = pd.concat([old, new]).drop_duplicates(keys, keep="last").reset_index(drop=True)
    return merged, {"added": added, "updated": updated, "unchanged": unchanged, "removed": 0}


class GraphStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def nodes_path(self) -> Path:
        return self.root / "nodes.parquet"

    @property
    def edges_dir(self) -> Path:
        return self.root / "edges"

    @property
    def manifest_path(self) -> Path:
        return self.root / "graph_manifest.json"

    @property
    def history_path(self) -> Path:
        return self.root / "graph_history.jsonl"

    # ---- 写 -----------------------------------------------------------------
    def write(
        self,
        graph: GraphData,
        *,
        mode: str = "full",
        delete_missing: bool = False,
    ) -> dict[str, Path]:
        if mode not in {"full", "incremental"}:
            raise ValueError(f"未知图谱模式: {mode}（可选 full / incremental）")

        self.root.mkdir(parents=True, exist_ok=True)
        self.edges_dir.mkdir(parents=True, exist_ok=True)
        previous_manifest = self.read_manifest() if self.manifest_path.exists() else {}

        if mode == "full":
            paths, stored_nodes, stored_edges, delta = self._write_full(graph)
        else:
            paths, stored_nodes, stored_edges, delta = self._write_incremental(
                graph, previous_manifest, delete_missing
            )

        manifest = dict(graph.manifest)
        manifest["objects"] = {str(k): int(v) for k, v in stored_nodes.value_counts("object_type").items()}
        manifest["relations"] = {name: int(len(frame)) for name, frame in stored_edges.items()}
        manifest["specs"] = {
            **previous_manifest.get("specs", {}),
            **graph.manifest.get("specs", {}),
        }
        manifest["run_id"] = uuid.uuid4().hex[:12]
        manifest["mode"] = mode
        manifest["delete_missing"] = bool(delete_missing)
        manifest["delta"] = delta
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._append_history(manifest)
        paths["manifest"] = self.manifest_path

        # 差量也回写给调用方（写进 GraphData，便于统计/日志）
        graph.manifest.update(
            {
                "run_id": manifest["run_id"],
                "mode": mode,
                "delta": delta,
                "objects": manifest["objects"],
                "relations": manifest["relations"],
            }
        )
        return paths

    def _write_full(self, graph: GraphData) -> tuple[dict[str, Path], pd.DataFrame, dict[str, pd.DataFrame], dict]:
        old_nodes = self.read_nodes() if self.nodes_path.exists() else pd.DataFrame()
        old_edges: dict[str, pd.DataFrame] = {}
        for path in self.edge_files():
            try:
                old_edges[path.stem] = pd.read_parquet(path)
            except Exception:  # 无效/残留文件不参与差量，写入时会被清掉
                continue

        if self.edges_dir.exists():
            shutil.rmtree(self.edges_dir)
        self.edges_dir.mkdir(parents=True, exist_ok=True)

        graph.nodes.to_parquet(self.nodes_path, index=False)
        paths: dict[str, Path] = {"nodes": self.nodes_path}
        for name, frame in graph.edges.items():
            path = self.edges_dir / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            paths[name] = path

        delta = {
            "nodes": {
                "added": int(len(graph.nodes)),
                "updated": 0,
                "unchanged": 0,
                "removed": int(len(old_nodes)),
            },
            "edges": {
                "added": int(sum(len(frame) for frame in graph.edges.values())),
                "updated": 0,
                "unchanged": 0,
                "removed": int(sum(len(frame) for frame in old_edges.values())),
            },
        }
        return paths, graph.nodes, dict(graph.edges), delta

    def _write_incremental(
        self,
        graph: GraphData,
        previous_manifest: dict[str, Any],
        delete_missing: bool,
    ) -> tuple[dict[str, Path], pd.DataFrame, dict[str, pd.DataFrame], dict]:
        old_nodes = self.read_nodes() if self.nodes_path.exists() else pd.DataFrame()
        old_edges: dict[str, pd.DataFrame] = {}
        for path in self.edge_files():
            try:
                old_edges[path.stem] = pd.read_parquet(path)
            except Exception:
                continue

        stored_nodes, node_delta = _compare(old_nodes, graph.nodes, NODE_KEYS)
        if delete_missing:
            keep = set(graph.nodes["object_id"])
            before = len(stored_nodes)
            stored_nodes = stored_nodes[stored_nodes["object_id"].isin(keep)].reset_index(drop=True)
            node_delta["removed"] = int(before - len(stored_nodes))

        stored_edges: dict[str, pd.DataFrame] = {}
        edge_delta = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}
        relation_names = set(old_edges) | set(graph.edges)
        for name in relation_names:
            if delete_missing and name not in graph.edges:
                edge_delta["removed"] += int(len(old_edges.get(name, pd.DataFrame())))
                continue
            old_frame = old_edges.get(name, pd.DataFrame())
            new_frame = graph.edges.get(name, pd.DataFrame())
            merged, delta = _compare(old_frame, new_frame, EDGE_KEYS)
            stored_edges[name] = merged
            for key in edge_delta:
                edge_delta[key] += int(delta[key])

        stored_nodes.to_parquet(self.nodes_path, index=False)
        paths: dict[str, Path] = {"nodes": self.nodes_path}
        for name, frame in stored_edges.items():
            path = self.edges_dir / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            paths[name] = path

        return paths, stored_nodes, stored_edges, {"nodes": node_delta, "edges": edge_delta}

    def _append_history(self, manifest: dict[str, Any]) -> None:
        entry = {
            "run_id": manifest["run_id"],
            "at": datetime.now(timezone.utc).isoformat(),
            "mode": manifest["mode"],
            "delete_missing": manifest["delete_missing"],
            "objects": manifest["objects"],
            "relations": manifest["relations"],
            "delta": manifest["delta"],
        }
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---- 读 -----------------------------------------------------------------
    def read_nodes(self) -> pd.DataFrame:
        return pd.read_parquet(self.nodes_path)

    def read_edges(self, relation: str) -> pd.DataFrame:
        return pd.read_parquet(self.edges_dir / f"{relation}.parquet")

    def read_manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def read_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def edge_files(self) -> list[Path]:
        return sorted(self.edges_dir.glob("*.parquet"))
