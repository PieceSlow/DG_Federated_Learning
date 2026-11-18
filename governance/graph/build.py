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

from governance.concept_key import ConceptResolver, concept_object_id
from governance.graph.model import ObjectModel, ObjectSpec, RelationSpec, load_model
from governance.icd import icd_chapter

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


def _event_identity(
    object_type: str, attributes: dict[str, Any]
) -> tuple[str, str, str | None] | None:
    """事件节点的 (编码体系, 编码, 药名)，编码体系**与 clinical_events 一致**。

    图谱与标签必须用同一套编码体系命名概念键：诊断/操作都是 ``ICD{version}``，
    处方是 ``MIMIC_FORMULARY`` + 药品目录编码；药名单独带出来，供按药名映射 RXNORM。
    """
    if object_type in {"Diagnosis", "Procedure"}:
        version, code = attributes.get("icd_version"), attributes.get("icd_code")
        if version in (None, "") or not code:
            return None
        return f"ICD{int(float(version))}", str(code), None
    if object_type == "Prescription":
        code = attributes.get("formulary_drug_cd")
        if code in (None, "") or not str(code).strip():
            return None
        return "MIMIC_FORMULARY", str(code).strip(), attributes.get("drug")
    return None


def _fallback_semantic_type(object_type: str, attributes: dict[str, Any]) -> str:
    """没命中 UMLS 时的语义类型：诊断/操作降级为 ICD 章节，其余给 N/A。"""
    if object_type in {"Diagnosis", "Procedure"}:
        version, code = attributes.get("icd_version"), attributes.get("icd_code")
        if version not in (None, "") and code:
            return icd_chapter(int(float(version)), code)
        return "Unknown"
    return "N/A"


def build_concept_layer(
    graph: GraphData,
    concept_map: pd.DataFrame | None,
) -> GraphData:
    """在已有图谱上追加 Concept 节点与三类概念边。

    一条规则同时覆盖"有 UMLS"和"无 UMLS"：事件节点 → :class:`ConceptResolver`
    解析 CUI；命中就用 CUI 合并（``Concept:{cui}``），未命中的编码**也建节点**，
    退回编码级（``Concept:{编码体系}:{编码}``）。这样标签里的每个概念键在图谱上
    都找得到，两边不会一半对不上。

    概念节点主键统一由 :func:`governance.concept_key.concept_object_id` 生成，
    **与标签阶段的 Concept 对象使用同一个函数**。
    """
    resolver = ConceptResolver(concept_map)
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in CONCEPT_RELATIONS.values()}
    concept_meta: dict[str, dict[str, Any]] = {}

    for node in graph.nodes.itertuples():
        relation = CONCEPT_RELATIONS.get(node.object_type)
        if relation is None:
            continue
        try:
            attributes = json.loads(node.attributes or "{}")
        except json.JSONDecodeError:
            continue
        identity = _event_identity(node.object_type, attributes)
        if identity is None:
            continue
        system, code, display = identity
        hit = resolver.resolve(node.object_type, system, code, display)
        cui = hit.cui if hit else None
        object_id = concept_object_id(system, code, cui)
        semantic_type = (
            hit.sty.strip()
            if hit is not None and hit.sty and hit.sty.strip()
            else _fallback_semantic_type(node.object_type, attributes)
        )
        meta = concept_meta.setdefault(
            object_id,
            {
                "cui": cui,
                "name": hit.name if hit else None,
                "sty": semantic_type,
                "codes": set(),
                "source_table": "concept_map" if hit else "clinical_events",
            },
        )
        meta["codes"].add(f"{system}//{code}")
        records[relation].append(
            {
                "src_type": node.object_type,
                "src_id": node.object_id,
                "dst_type": "Concept",
                "dst_id": object_id,
                "relation": relation,
                "evidence": (
                    f"concept_map:{system}:{code}:{hit.method}"
                    if hit
                    else f"code_key:{system}:{code}"
                ),
                "confidence": hit.confidence if hit else 1.0,
            }
        )

    if not concept_meta:
        return graph

    concept_nodes = pd.DataFrame(
        [
            {
                "object_type": "Concept",
                "object_id": object_id,
                "attributes": json.dumps(
                    {
                        "cui": meta["cui"],
                        "name": meta["name"],
                        "sty": meta["sty"],
                        "codes": sorted(meta["codes"]),
                    },
                    ensure_ascii=False,
                ),
                "source_table": meta["source_table"],
            }
            for object_id, meta in sorted(concept_meta.items())
        ],
        columns=NODE_COLUMNS,
    )
    graph.nodes = pd.concat([graph.nodes, concept_nodes], ignore_index=True)
    for relation, rows in records.items():
        graph.edges[relation] = pd.DataFrame(rows, columns=EDGE_COLUMNS)
        graph.manifest["relations"][relation] = int(len(rows))
    graph.manifest["objects"]["Concept"] = int(len(concept_meta))
    mapped = sum(1 for meta in concept_meta.values() if meta["source_table"] == "concept_map")
    specs = graph.manifest.setdefault("specs", {})
    specs["Concept"] = {
        "keys": ["cui"] if mapped == len(concept_meta) else ["cui", "code_system", "code"],
        "attributes": ["name", "sty", "codes"],
        "source_table": "concept_map" if mapped == len(concept_meta) else "concept_map + clinical_events",
        "sensitivity": "L1",
        "concept": (
            "UMLS 概念（跨中心语义对齐锚点）"
            if mapped == len(concept_meta)
            else "UMLS 概念 + 编码级兜底（未命中 UMLS 的编码仍建节点，保证标签可 join）"
        ),
    }
    graph.manifest["note"] = graph.manifest.get("note", "") + (
        f" Concept 为 UMLS 派生对象（CUI {mapped} 个，编码级兜底 {len(concept_meta) - mapped} 个）。"
    )
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
        "specs": {
            name: {
                "keys": list(spec.keys),
                "attributes": list(spec.attributes),
                "source_table": spec.source_table,
                "sensitivity": spec.sensitivity,
                "concept": spec.concept,
            }
            for name, spec in model.objects.items()
        },
        "relation_specs": [
            {
                "name": relation.name,
                "from": relation.from_object,
                "to": relation.to_object,
                "on": relation.on,
                "cardinality": relation.cardinality,
            }
            for relation in model.relations
        ],
        "model_version": model.version,
        "note": "节点/边实例仅本地保存；manifest 只含计数，不含患者行。",
    }
    return GraphData(nodes=nodes, edges=edges, manifest=manifest)
