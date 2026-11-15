"""图谱实例化：按对象模型把表行变成节点与边。

* 节点：``object_type + object_id + attributes``；
* 边：``src/dst/relation/evidence/confidence``，**每种关系一张表**；
* 边只从外键（``hadm_id`` / ``subject_id``）推导，不做猜测；缺失的键不生成边，
  由 :mod:`governance.graph.verify` 报出来。

图是"本地资产"：节点属性含患者级信息，只落 ``output/``，不提交、不外发。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from governance.graph.model import ObjectModel, ObjectSpec, RelationSpec, load_model

NODE_COLUMNS = ["object_type", "object_id", "attributes", "source_table"]
EDGE_COLUMNS = ["src_type", "src_id", "dst_type", "dst_id", "relation", "evidence", "confidence"]


@dataclass
class GraphData:
    nodes: pd.DataFrame
    edges: dict[str, pd.DataFrame] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, dict[str, int]]:
        return {
            "objects": self.manifest.get("objects", {}),
            "relations": self.manifest.get("relations", {}),
        }


def _clean(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def make_object_id(object_name: str, values: list[Any]) -> str | None:
    parts = [_clean(value) for value in values]
    if any(part is None for part in parts):
        return None
    return f"{object_name}:{':'.join(parts)}"


def _node_frame(object_name: str, spec: ObjectSpec, rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        object_id = make_object_id(object_name, [row.get(key) for key in spec.keys])
        if object_id is None:
            continue  # 主键缺失的行不建节点；由质量规则暴露
        attributes = {
            name: _clean(row.get(name))
            for name in spec.attributes
            if name in rows.columns
        }
        records.append(
            {
                "object_type": object_name,
                "object_id": object_id,
                "attributes": json.dumps(attributes, ensure_ascii=False),
                "source_table": spec.source_table,
            }
        )
    return pd.DataFrame(records, columns=NODE_COLUMNS)


def _edge_frame(
    relation: RelationSpec,
    from_spec: ObjectSpec,
    to_spec: ObjectSpec,
    rows: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    if rows.empty or relation.on not in rows.columns:
        return pd.DataFrame(records, columns=EDGE_COLUMNS)
    for index, row in rows.iterrows():
        src_id = make_object_id(relation.from_object, [row.get(relation.on)])
        dst_id = make_object_id(relation.to_object, [row.get(key) for key in to_spec.keys])
        if src_id is None or dst_id is None:
            continue
        records.append(
            {
                "src_type": relation.from_object,
                "src_id": src_id,
                "dst_type": relation.to_object,
                "dst_id": dst_id,
                "relation": relation.name,
                "evidence": f"{to_spec.source_table}:{index}",
                "confidence": 1.0,
            }
        )
    return pd.DataFrame(records, columns=EDGE_COLUMNS)


def build_graph(
    tables: Mapping[str, pd.DataFrame],
    notes: pd.DataFrame | None = None,
    model: ObjectModel | None = None,
) -> GraphData:
    """从表数据构建图谱实例。

    ``notes`` 为出院小结对象表（``note_id/subject_id/hadm_id/charttime/note_type``），
    因为文本对象来自事件流而不是某张结构化源表。
    """
    model = model or load_model()
    sources: dict[str, pd.DataFrame] = dict(tables)
    if notes is not None:
        sources["discharge"] = notes

    node_frames: list[pd.DataFrame] = []
    object_counts: dict[str, int] = {}
    for name, spec in model.objects.items():
        rows = sources.get(spec.source_table)
        frame = _node_frame(name, spec, rows) if rows is not None else pd.DataFrame(columns=NODE_COLUMNS)
        node_frames.append(frame)
        object_counts[name] = int(len(frame))
    nodes = (
        pd.concat(node_frames, ignore_index=True)
        if node_frames
        else pd.DataFrame(columns=NODE_COLUMNS)
    )

    edges: dict[str, pd.DataFrame] = {}
    relation_counts: dict[str, int] = {}
    for relation in model.relations:
        to_spec = model.objects[relation.to_object]
        from_spec = model.objects[relation.from_object]
        rows = sources.get(to_spec.source_table)
        frame = (
            _edge_frame(relation, from_spec, to_spec, rows)
            if rows is not None
            else pd.DataFrame(columns=EDGE_COLUMNS)
        )
        edges[relation.name] = frame
        relation_counts[relation.name] = int(len(frame))

    manifest = {
        "version": model.version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "objects": object_counts,
        "relations": relation_counts,
        "sources": sorted({spec.source_table for spec in model.objects.values()}),
        "note": "节点/边实例仅本地保存；manifest 只含计数，不含患者行。",
    }
    return GraphData(nodes=nodes, edges=edges, manifest=manifest)
