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
from governance.graph.build import build_graph
from governance.graph.store import GraphStore
from governance.graph.verify import verify as verify_graph

#: 对象关系（一对多），与上图一致
RELATIONS = [
    {"from": "Patient", "to": "Encounter", "on": "subject_id", "cardinality": "1:N"},
    {"from": "Encounter", "to": "ICUStay", "on": "hadm_id", "cardinality": "1:N"},
    {"from": "Encounter", "to": "ClinicalNote", "on": "hadm_id", "cardinality": "1:N"},
    {"from": "Encounter", "to": "Diagnosis", "on": "hadm_id", "cardinality": "1:N"},
    {"from": "Encounter", "to": "Prescription", "on": "hadm_id", "cardinality": "1:N"},
    {"from": "Encounter", "to": "Procedure", "on": "hadm_id", "cardinality": "1:N"},
]


def _objects(ctx: GovernanceContext) -> list[dict]:
    tables = ctx.tables
    return [
        {
            "object": "Patient",
            "source_table": "patients",
            "grain": "1 行/患者",
            "keys": ["subject_id"],
            "attributes": ["gender", "anchor_age", "anchor_year"],
            "rows": int(len(tables["patients"])),
            "sensitivity": "L3",
        },
        {
            "object": "Encounter",
            "source_table": "admissions",
            "grain": "1 行/住院",
            "keys": ["hadm_id"],
            "attributes": ["admittime", "dischtime", "admission_type", "hospital_expire_flag"],
            "rows": int(len(tables["admissions"])),
            "sensitivity": "L3",
        },
        {
            "object": "ICUStay",
            "source_table": "icustays",
            "grain": "1 行/ICU 停留",
            "keys": ["stay_id"],
            "attributes": ["intime", "outtime", "first_careunit", "last_careunit"],
            "rows": int(len(tables["icustays"])),
            "sensitivity": "L3",
        },
        {
            "object": "ClinicalNote",
            "source_table": "discharge",
            "grain": "1 行/出院小结",
            "keys": ["note_id"],
            "attributes": ["charttime", "text"],
            "rows": int(ctx.stats.get("notes_used", 0)),
            "sensitivity": "L3",
        },
        {
            "object": "Diagnosis",
            "source_table": "diagnoses_icd",
            "grain": "1 行/诊断编码",
            "keys": ["subject_id", "hadm_id", "seq_num"],
            "attributes": ["icd_code", "icd_version"],
            "rows": int(len(tables["diagnoses_icd"])),
            "sensitivity": "L2",
        },
        {
            "object": "Prescription",
            "source_table": "prescriptions",
            "grain": "1 行/处方",
            "keys": ["subject_id", "hadm_id", "pharmacy_id"],
            "attributes": ["starttime", "drug", "formulary_drug_cd", "dose_val_rx"],
            "rows": int(len(tables["prescriptions"])),
            "sensitivity": "L2",
        },
        {
            "object": "Procedure",
            "source_table": "procedures_icd",
            "grain": "1 行/操作编码",
            "keys": ["subject_id", "hadm_id", "seq_num"],
            "attributes": ["chartdate", "icd_code", "icd_version"],
            "rows": int(len(tables["procedures_icd"])),
            "sensitivity": "L2",
        },
    ]


def run(ctx: GovernanceContext) -> None:
    objects = _objects(ctx)
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
        "relations": RELATIONS,
    }
    ctx.object_graph = graph

    metadata = ctx.settings.governance / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "object_graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(objects).to_csv(
        metadata / "object_inventory.csv", index=False, encoding="utf-8-sig"
    )
    graph_data = build_graph(ctx.tables, notes=_note_frame(ctx))
    GraphStore(ctx.settings.governance / "graph").write(graph_data)

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
        f"定义 {len(objects)} 类对象、{len(RELATIONS)} 条关系；"
        f"实例化 {int(len(graph_data.nodes))} 个节点、"
        f"{sum(len(frame) for frame in graph_data.edges.values())} 条边；"
        f"图谱校验发现 {len(graph_issues)} 项问题。",
    )


def _note_frame(ctx: GovernanceContext) -> pd.DataFrame:
    """从事件流还原文书对象（文本对象不在结构化源表里）。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for event in ctx.events:
        if event.get("code") != "NOTE//DISCHARGE":
            continue
        record = str(event.get("source_record_id") or "")
        note_id = record.split(":", 1)[1] if ":" in record else record
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        rows.append(
            {
                "note_id": note_id,
                "subject_id": event.get("subject_id"),
                "hadm_id": event.get("hadm_id"),
                "charttime": event.get("time"),
                "note_type": "DS",
            }
        )
    return pd.DataFrame(rows, columns=["note_id", "subject_id", "hadm_id", "charttime", "note_type"])
