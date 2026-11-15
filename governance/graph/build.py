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

#: UMLS 概念层：把事件对象挂到概念（派生对象，不来自 MIMIC 表）
CONCEPT_RELATIONS = {
    "Diagnosis": "diagnosis_concept",
    "Prescription": "prescription_concept",
    "Procedure": "procedure_concept",
}
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


def _concept_key(object_type: str, attributes: dict[str, Any]) -> tuple[str, str] | None:
    if object_type == "Diagnosis":
        version = attributes.get("icd_version")
        code = attributes.get("icd_code")
        return (f"ICD{int(float(version))}", str(code)) if version and code else None
    if object_type == "Procedure":
        version = attributes.get("icd_version")
        code = attributes.get("icd_code")
        if not version or not code:
            return None
        system = "ICD10PCS" if int(float(version)) == 10 else "ICD9CM_PROC"
        return system, str(code)
    if object_type == "Prescription":
        drug = attributes.get("drug")
        return ("RXNORM", str(drug)) if drug else None
    return None


def build_concept_layer(graph: GraphData, concept_map: pd.DataFrame | None) -> GraphData:
    """在已有图谱上追加 Concept 节点与三类概念边。

    ``concept_map`` 来自概念归一阶段；为空时原样返回（不改变原有 7 类对象）。
    """
    if concept_map is None or concept_map.empty:
        return graph
    index: dict[tuple[str, str], tuple[str, str, str, float, str]] = {}
    for row in concept_map.itertuples():
        index[(str(row.source_system), str(row.source_code))] = (
            str(row.cui), str(row.concept_name), str(row.sty),
            float(row.confidence), str(row.method),
        )

    records: dict[str, list[dict[str, Any]]] = {name: [] for name in CONCEPT_RELATIONS.values()}
    concept_meta: dict[str, tuple[str, str]] = {}
    for node in graph.nodes.itertuples():
        relation = CONCEPT_RELATIONS.get(node.object_type)
        if relation is None:
            continue
        try:
            attributes = json.loads(node.attributes or "{}")
        except json.JSONDecodeError:
            continue
        key = _concept_key(node.object_type, attributes)
        if key is None:
            continue
        hit = index.get(key)
        if hit is None:
            continue
        cui, name, sty, confidence, method = hit
        concept_meta[cui] = (name, sty)
        records[relation].append(
            {
                "src_type": node.object_type,
                "src_id": node.object_id,
                "dst_type": "Concept",
                "dst_id": f"Concept:{cui}",
                "relation": relation,
                "evidence": f"concept_map:{key[0]}:{key[1]}:{method}",
                "confidence": confidence,
            }
        )

    if not concept_meta:
        return graph

    concept_nodes = pd.DataFrame(
        [
            {
                "object_type": "Concept",
                "object_id": f"Concept:{cui}",
                "attributes": json.dumps({"name": name, "sty": sty}, ensure_ascii=False),
                "source_table": "concept_map",
            }
            for cui, (name, sty) in sorted(concept_meta.items())
        ],
        columns=NODE_COLUMNS,
    )
    graph.nodes = pd.concat([graph.nodes, concept_nodes], ignore_index=True)
    for relation, rows in records.items():
        graph.edges[relation] = pd.DataFrame(rows, columns=EDGE_COLUMNS)
        graph.manifest["relations"][relation] = int(len(rows))
    graph.manifest["objects"]["Concept"] = int(len(concept_meta))
    graph.manifest["note"] = graph.manifest.get("note", "") + " Concept 为 UMLS 派生对象。"
    return graph


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
