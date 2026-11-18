"""阶段 09 · 标签化（KV 对象标签）。

标签不是"再算几个字段"，而是打在数据对象上的 KV 断言：
``object_type + object_id + tag_key + tag_value``。本阶段同时输出：

* ``labels/object_labels.parquet``：对象标签长表（L1–L4）；
* ``labels/tag_dictionary.csv``：标签字典（定义、层、口径、来源）；
* ``metadata/labels.json``：字段统计 + 标签体系汇总 + 覆盖率。

"医学标签强化"只对应 L3 概念级（``L3.cui`` / ``L3.semantic_type``），由 UMLS
``concept_map`` 提供；没有 UMLS 时 ``L3.semantic_type`` 降级为 ICD 章节，不生成 CUI。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from governance.concept_key import concept_object_id
from governance.context import GovernanceContext, require_state
from governance.labels import (
    ObjectLabelStore,
    compute_labels,
    definitions_catalog,
    load_definitions,
)
from governance.text_notes import note_frame

#: 敏感级别：对象类型 → 级别
SENSITIVITY = {
    "Diagnosis": "L2",
    "Procedure": "L2",
    "Prescription": "L2",
    "ClinicalNote": "L3",
}

#: 字段统计维度：这些是列/元数据，不是 KV 标签。
FIELD_STATISTICS = {
    "event_type": "对象类型：诊疗事件属于哪一类对象",
    "code_system": "术语体系：编码来自哪套标准",
    "icd_chapter": "语义章节：按 ICD 编码归入的疾病/操作章节",
    "sensitivity": "敏感级别：L1 公开 / L2 受控共享 / L3 仅本地",
    "split": "患者级划分：train / tuning / held_out，防止联邦训练泄漏",
}


def sensitivity_for(event_type: str) -> str:
    return SENSITIVITY.get(event_type, "L2")


def _semantic_type_counts(concept_map: pd.DataFrame | None) -> dict[str, int]:
    """按 CUI 统计 UMLS 语义类型（概念数，不是患者数）。"""
    if concept_map is None or concept_map.empty or "sty" not in concept_map.columns:
        return {}
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for cui, sty in zip(concept_map["cui"], concept_map["sty"]):
        cui = str(cui)
        if cui in seen:
            continue
        seen.add(cui)
        for part in str(sty).split("|"):
            part = part.strip()
            if part:
                counts[part] += 1
    return dict(counts.most_common(15))


def _value_counts(series: pd.Series, top: int | None = None) -> dict[str, int]:
    counts = series.fillna("(空)").value_counts()
    if top:
        counts = counts.head(top)
    return {str(k): int(v) for k, v in counts.items()}


def _concept_ids(events: pd.DataFrame, concept_map: pd.DataFrame | None) -> set[str]:
    """概念对象主键：与图谱 Concept 节点用同一个函数生成。"""
    if events is None or events.empty:
        return set()
    pairs = events[["code_system", "code"]].dropna().drop_duplicates()
    pairs = pairs[pairs["code"].astype(str).str.strip() != ""]
    cui_by_code: dict[tuple[str, str], str] = {}
    if concept_map is not None and not concept_map.empty:
        for row in concept_map.itertuples():
            cui_by_code[(str(row.source_system), str(row.source_code))] = str(row.cui)
    return {
        concept_object_id(system, code, cui_by_code.get((str(system), str(code))))
        for system, code in pairs.itertuples(index=False)
    }


def _coverage(
    object_labels: pd.DataFrame,
    events: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    concept_map: pd.DataFrame | None = None,
    graph=None,
) -> dict[str, dict]:
    def covered(object_type: str) -> set[str]:
        subset = object_labels[object_labels["object_type"] == object_type]
        return set(subset["object_id"])

    event_ids = {"Event:" + str(value) for value in events["record_id"]}
    patient_ids = {"Patient:" + str(value) for value in tables["patients"]["subject_id"]}
    concept_ids = _concept_ids(events, concept_map)

    graph_concepts: set[str] = set()
    nodes = getattr(graph, "nodes", None) if graph is not None else None
    if isinstance(nodes, pd.DataFrame) and not nodes.empty and "object_type" in nodes:
        graph_concepts = set(nodes.loc[nodes["object_type"] == "Concept", "object_id"])

    return {
        "Event": {
            "total": len(event_ids),
            "labeled": len(covered("Event") & event_ids),
            "ratio": round(len(covered("Event") & event_ids) / len(event_ids), 4) if event_ids else 0.0,
        },
        "Patient": {
            "total": len(patient_ids),
            "labeled": len(covered("Patient") & patient_ids),
            "ratio": round(len(covered("Patient") & patient_ids) / len(patient_ids), 4) if patient_ids else 0.0,
        },
        "Concept": {
            "total": len(concept_ids),
            "labeled": len(covered("Concept") & concept_ids),
            "ratio": round(len(covered("Concept") & concept_ids) / len(concept_ids), 4) if concept_ids else 0.0,
            "graph_nodes": len(graph_concepts),
            "graph_matched": len(concept_ids & graph_concepts),
            # 标签里的概念键有多少能落到图谱 Concept 节点上（ratio 只说明"标签打上了"，
            # 不代表图谱也有；无 UMLS 时文书概念不建节点，会比 1.0 少一点）
            "graph_ratio": (
                round(len(concept_ids & graph_concepts) / len(concept_ids), 4)
                if concept_ids
                else 0.0
            ),
            "graph_missing": len(concept_ids - graph_concepts),
        },
    }


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "graph")
    events = ctx.clinical_events

    version, definitions = load_definitions()
    graph_issues: list[dict] = []
    object_labels = compute_labels(
        ctx.tables,
        notes=note_frame(ctx),
        events=events,
        concept_map=getattr(ctx, "concept_map", None),
        manual_path=ctx.settings.manual_labels,
        definitions=definitions,
        version=version,
        graph=ctx.graph,
        issues=graph_issues,
    )
    for issue in graph_issues:
        ctx.add_issue(
            issue["rule"],
            issue["severity"],
            "labels",
            issue["detail"],
            subject=None,
            record=None,
        )
    ObjectLabelStore(ctx.settings.governance / "labels").write(object_labels)

    tag_totals = (
        {str(k): int(v) for k, v in object_labels.groupby("tag_key").size().items()}
        if not object_labels.empty
        else {}
    )
    # 同一个 tag_key 会打在 Event/Patient/Dataset 等不同对象上，计数必须按 (tag_key, object)
    object_counts = (
        {
            (str(k[0]), str(k[1])): int(v)
            for k, v in object_labels.groupby(["tag_key", "object_type"]).size().items()
        }
        if not object_labels.empty
        else {}
    )
    layer_counts = (
        {str(k): int(v) for k, v in object_labels.groupby("layer").size().items()}
        if not object_labels.empty
        else {}
    )

    dictionary = pd.DataFrame(definitions_catalog(definitions))
    dictionary["count"] = [
        object_counts.get((str(row.tag_key), str(row.object)), 0)
        for row in dictionary.itertuples()
    ]
    labels_dir = ctx.settings.governance / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    dictionary.to_csv(labels_dir / "tag_dictionary.csv", index=False, encoding="utf-8-sig")

    coverage = _coverage(
        object_labels, events, ctx.tables,
        concept_map=getattr(ctx, "concept_map", None), graph=ctx.graph,
    )
    labels = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label_system": {
            "version": version,
            "note": "标签 = 打在对象上的 KV；L1 资产 / L2 记录 / L3 概念 / L4 断言",
            "layers": layer_counts,
            "tag_keys": tag_totals,
        },
        "coverage": coverage,
        "concept": {
            "umls_enabled": getattr(ctx, "concept_map", None) is not None,
            "semantic_type_counts": _semantic_type_counts(getattr(ctx, "concept_map", None)),
            "graph_nodes": coverage["Concept"]["graph_nodes"],
            "graph_matched": coverage["Concept"]["graph_matched"],
            "note": (
                "概念对象主键由 concept_key.concept_object_id 生成，与图谱 Concept 节点一致；"
                "有 UMLS 用 CUI，无 UMLS 用 编码体系:编码 降级"
            ),
        },
        "field_statistics": {
            key: {
                "description": description,
                "values": _value_counts(
                    events[key], top=10 if key == "icd_chapter" else None
                ),
            }
            for key, description in FIELD_STATISTICS.items()
            if key in events
        },
        "object_labels": {
            "version": version,
            "definitions": definitions_catalog(definitions),
            "counts": tag_totals,
            "manual_entries": int((object_labels["source"] == "manual").sum())
            if not object_labels.empty
            else 0,
            "note": "标签打在对象上（object_type/object_id），tag_value 为字符串",
        },
    }

    ctx.object_labels = object_labels
    ctx.stats["object_labels"] = {
        "rows": int(len(object_labels)),
        "keys": tag_totals,
        "layers": layer_counts,
        "manual": labels["object_labels"]["manual_entries"],
        "coverage": coverage,
    }

    ctx.labels = labels
    (ctx.settings.governance / "metadata" / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ctx.settings.governance / "quality" / "label_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.log(
        "标签化",
        f"KV 标签 {len(object_labels)} 条，覆盖 {len(tag_totals)} 个 tag_key；"
        f"层分布 {layer_counts}；概念覆盖率 {coverage['Concept']['ratio']:.1%}"
        f"（图谱命中 {coverage['Concept']['graph_ratio']:.1%}）。",
    )
