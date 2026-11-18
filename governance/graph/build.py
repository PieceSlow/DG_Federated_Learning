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

from governance.concept_key import concept_object_id
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


def _event_code_key(object_type: str, attributes: dict[str, Any]) -> tuple[str, str] | None:
    """事件层的编码键，与 ``assemble`` 生成 ``code_system``/``code`` 的规则一致。

    UMLS 的 ``_concept_key`` 用的是源词表 SAB（``ICD10PCS``/``RXNORM``），
    和 ``clinical_events.code_system``（``ICD10``/``MIMIC_FORMULARY``）不是一回事；
    没有 UMLS 时概念层必须按事件口径降级，否则又会造出第三套主键。
    """
    if object_type in {"Diagnosis", "Procedure"}:
        version, code = attributes.get("icd_version"), attributes.get("icd_code")
        if version in (None, "") or not code:
            return None
        return f"ICD{int(float(version))}", str(code)
    if object_type == "Prescription":
        code = attributes.get("formulary_drug_cd")
        return ("MIMIC_FORMULARY", str(code).strip()) if code not in (None, "") else None
    return None


def _code_semantic_type(object_type: str, attributes: dict[str, Any]) -> str:
    """无 UMLS 时的语义类型：诊断/操作降级为 ICD 章节，其余与事件 ``icd_chapter`` 兜底一致。"""
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

    两种模式，都由**图谱里已有的事件节点**推导，保证"有边必有节点、有节点必可达"：

    * **UMLS 模式**（``concept_map`` 非空）：概念节点按 CUI 合并，键为 ``Concept:{cui}``，
      只收已归一的编码；
    * **降级模式**（无 UMLS）：概念节点按事件编码建立，键为 ``Concept:{编码体系}:{编码}``，
      语义类型降级为 ICD 章节。没有这一层，图谱就没有概念对象，而标签却会打出
      L3 概念标签，两边对不上。

    概念节点主键统一由 :func:`governance.concept_key.concept_object_id` 生成，
    **与标签阶段的 Concept 对象使用同一个函数**。
    """
    umls = concept_map is not None and not concept_map.empty
    index: dict[tuple[str, str], tuple[str, str, str, float, str]] = {}
    if umls:
        for row in concept_map.itertuples():
            index[(str(row.source_system), str(row.source_code))] = (
                str(row.cui), str(row.concept_name), str(row.sty),
                float(row.confidence), str(row.method),
            )

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

        if umls:
            key = _concept_key(node.object_type, attributes)
            if key is None:
                continue
            hit = index.get(key)
            if hit is None:
                continue
            cui, name, sty, confidence, method = hit
            evidence = f"concept_map:{key[0]}:{key[1]}:{method}"
        else:
            key = _event_code_key(node.object_type, attributes)
            if key is None:
                continue
            cui, name = None, None
            sty = _code_semantic_type(node.object_type, attributes)
            confidence, method = 1.0, "code_key"
            evidence = f"code_key:{key[0]}:{key[1]}"

        object_id = concept_object_id(key[0], key[1], cui)
        meta = concept_meta.setdefault(
            object_id,
            {"cui": cui, "name": name, "sty": sty, "codes": set()},
        )
        meta["codes"].add(f"{key[0]}//{key[1]}")
        records[relation].append(
            {
                "src_type": node.object_type,
                "src_id": node.object_id,
                "dst_type": "Concept",
                "dst_id": object_id,
                "relation": relation,
                "evidence": evidence,
                "confidence": confidence,
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
                "source_table": "concept_map" if umls else "clinical_events",
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
    specs = graph.manifest.setdefault("specs", {})
    specs["Concept"] = {
        "keys": ["cui"] if umls else ["code_system", "code"],
        "attributes": ["name", "sty", "codes"],
        "source_table": "concept_map" if umls else "clinical_events",
        "sensitivity": "L1",
        "concept": (
            "UMLS 概念（跨中心语义对齐锚点）"
            if umls
            else "编码级概念（无 UMLS 时的降级节点，语义类型取 ICD 章节）"
        ),
    }
    graph.manifest["note"] = graph.manifest.get("note", "") + (
        " Concept 为 UMLS 派生对象。" if umls else " Concept 为编码级降级节点（无 UMLS）。"
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
