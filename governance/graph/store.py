"""图谱存储：节点表 + 每种关系一张边表 + manifest。

边当表存的直接好处：可以用 pandas/SQL 直接查（"某患者的所有就诊与诊断"就是 join），
新增数据可以 append，不需要重建整张图，也不用为了课程作业去部署图数据库。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from governance.graph.build import GraphData


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

    def write(self, graph: GraphData) -> dict[str, Path]:
        """整图重写（同一输入幂等）；后续增量更新再引入 append。"""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.edges_dir.exists():
            shutil.rmtree(self.edges_dir)  # 清掉上一次的边类型，避免残留
        self.edges_dir.mkdir(parents=True, exist_ok=True)

        graph.nodes.to_parquet(self.nodes_path, index=False)
        paths = {"nodes": self.nodes_path}
        for name, frame in graph.edges.items():
            path = self.edges_dir / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            paths[name] = path
        self.manifest_path.write_text(
            json.dumps(graph.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths["manifest"] = self.manifest_path
        return paths

    def read_nodes(self) -> pd.DataFrame:
        return pd.read_parquet(self.nodes_path)

    def read_edges(self, relation: str) -> pd.DataFrame:
        return pd.read_parquet(self.edges_dir / f"{relation}.parquet")

    def read_manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def edge_files(self) -> list[Path]:
        return sorted(self.edges_dir.glob("*.parquet"))
