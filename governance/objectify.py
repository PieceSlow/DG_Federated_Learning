"""阶段 07 · 对象化。

把"表"翻译成"对象"：每个对象有主键、属性、粒度、敏感级别，对象之间有明确的关系。
这一步的产物是对象图谱（``object_graph.json``）与对象清单（``object_inventory.csv``），
它们是后续编目、共享与联邦建模的共同语言。

7 类对象、6 条关系：

```text
Patient ─1:N─ Encounter ─1:N─ ICUStay
                  ├─1:N─ ClinicalNote
                  ├─1:N─ Diagnosis
                  ├─1:N─ Prescription
                  └─1:N─ Procedure
```
"""

from __future__ import annotations

import json

import pandas as pd

from governance.context import GovernanceContext
from governance.graph.agentic import build_with_agents
from governance.graph.build import build_concept_layer, build_graph
from governance.graph.store import GraphStore
from governance.graph.verify import verify as verify_graph
from governance.text_notes import note_frame

#: 对象关系（一对多），与上图一致
CONCEPT_RELATIONS_SPEC = [
    {"from": "Diagnosis", "to": "Concept", "on": "cui", "cardinality": "1:N"},
    {"from": "Prescription", "to": "Concept", "on": "cui", "cardinality": "1:N"},
    {"from": "Procedure", "to": "Concept", "on": "cui", "cardinality": "1:N"},
]


def _objects_from_graph(graph_data) -> list[dict]:
    """对象清单直接来自图谱 manifest（智能体提案或默认模型），保证口径一致。"""
    specs = graph_data.manifest.get("specs", {})
    counts = graph_data.manifest.get("objects", {})
    items: list[dict] = []
    for name, rows in counts.items():
        spec = specs.get(name, {})
        items.append(
            {
                "object": name,
                "source_table": spec.get("source_table", ""),
                "grain": "1 行/对象",
                "keys": spec.get("keys", []),
                "attributes": spec.get("attributes", []),
                "rows": int(rows),
                "sensitivity": spec.get("sensitivity", "L2"),
            }
        )
    return items


def _relations_from_graph(graph_data) -> list[dict]:
    return [
        {
            "from": item["from"],
            "to": item["to"],
            "on": item["on"],
            "cardinality": item.get("cardinality", "1:N"),
        }
        for item in graph_data.manifest.get("relation_specs", [])
    ]


def run(ctx: GovernanceContext) -> None:
    notes = note_frame(ctx)
    graph_data = None
    if ctx.llm is not None and ctx.llm.config.provider != "off":
        graph_data, errors = build_with_agents(ctx, notes)
        if graph_data is None:
            ctx.log(
                "智能体建图",
                "回退到确定性对象模型：" + "；".join(errors[:3]),
            )
    if graph_data is None:
        graph_data = build_graph(ctx.tables, notes=notes)
    graph_data = build_concept_layer(graph_data, ctx.concept_map)

    objects = _objects_from_graph(graph_data)
    relations = _relations_from_graph(graph_data)
    if int(graph_data.manifest["objects"].get("Concept", 0)):
        relations.extend(CONCEPT_RELATIONS_SPEC)

    graph = {
        "objects": {
            item["object"]: {
                "keys": item["keys"],
                "attributes": item["attributes"],
                "rows": item["rows"],
                "source_table": item["source_table"],
                "grain": item["grain"],
                "sensitivity": item["sensitivity"],
            }
            for item in objects
        },
        "relations": relations,
    }
    ctx.object_graph = graph
    # 节点与边也要放上上下文：标签阶段要按图谱（而不是宽表）算断言。
    ctx.graph = graph_data

    metadata = ctx.settings.governance / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "object_graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(objects).to_csv(
        metadata / "object_inventory.csv", index=False, encoding="utf-8-sig"
    )
    GraphStore(ctx.settings.governance / "graph").write(
        graph_data,
        mode=ctx.settings.graph_mode,
        delete_missing=ctx.settings.graph_delete_missing,
    )

    graph_issues = verify_graph(graph_data)
    for issue in graph_issues:
        ctx.add_issue(
            issue["rule"],
            issue["severity"],
            "graph",
            issue["detail"],
            subject=None,
            record=issue["record"],
        )

    # 图谱问题并入质量清单（quality 阶段已写过一次，这里追加后重写）
    ctx.issues_frame().to_csv(
        ctx.settings.governance / "quality" / "issues.csv",
        index=False,
        encoding="utf-8-sig",
    )

    ctx.stats.update(
        {
            "graph_objects": graph_data.manifest["objects"],
            "graph_relations": graph_data.manifest["relations"],
            "graph_issues": len(graph_issues),
        }
    )
    ctx.log(
        "对象化",
        f"定义 {len(objects)} 类对象、{len(relations)} 条关系；"
        f"实例化 {int(len(graph_data.nodes))} 个节点、"
        f"{sum(len(frame) for frame in graph_data.edges.values())} 条边；"
        f"图谱校验发现 {len(graph_issues)} 项问题。",
    )

