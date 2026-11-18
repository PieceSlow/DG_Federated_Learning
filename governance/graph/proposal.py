"""把智能体提出的对象/关系候选，转换为可执行的对象模型并校验。

原则：**LLM 提议，代码执行**。提出的对象与关系必须能对上真实表结构：
源表存在、主键在列里、关系的 join 列存在；否则一律拒绝，由 Supervisor 带反馈重试。
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from governance.graph.model import ObjectModel, ObjectSpec, RelationSpec


def _sources(tables: Mapping[str, pd.DataFrame], notes: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    sources = dict(tables)
    if notes is not None:
        sources["discharge"] = notes
    return sources


def validate_proposals(
    objects: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    tables: Mapping[str, pd.DataFrame],
    notes: pd.DataFrame | None = None,
) -> list[str]:
    """返回错误列表；为空表示提案可执行。"""
    errors: list[str] = []
    sources = _sources(tables, notes)
    specs: dict[str, dict[str, Any]] = {}

    for item in objects:
        name = str(item.get("name") or "").strip()
        table = str(item.get("source_table") or "").strip()
        keys = [str(key) for key in item.get("keys") or []]
        if not name:
            errors.append("对象缺少 name")
            continue
        if not keys:
            errors.append(f"{name}: 缺少主键")
            continue
        if table not in sources:
            errors.append(f"{name}: 源表 {table} 不存在")
            continue
        missing = [key for key in keys if key not in sources[table].columns]
        if missing:
            errors.append(f"{name}: 主键不在源表中: {', '.join(missing)}")
            continue
        if name in specs:
            errors.append(f"对象重名: {name}")
            continue
        frame = sources[table]
        if len(frame) <= 500_000:  # 大表跳过，避免校验拖慢流水线
            if frame.duplicated(subset=keys).any():
                errors.append(f"{name}: 主键不唯一（{', '.join(keys)}），不能区分行")
                continue
        specs[name] = {"table": table, "keys": keys}

    for relation in relations:
        name = str(relation.get("name") or "").strip()
        source = str(relation.get("from") or "")
        target = str(relation.get("to") or "")
        on = str(relation.get("on") or "")
        if not name or not source or not target or not on:
            errors.append(f"关系字段不完整: {relation}")
            continue
        if source not in specs or target not in specs:
            errors.append(f"{name}: 引用了未定义的对象 {source}/{target}")
            continue
        if on not in specs[source]["keys"]:
            errors.append(f"{name}: on={on} 必须是 {source} 的主键")
            continue
        target_table = specs[target]["table"]
        if on not in sources[target_table].columns:
            errors.append(f"{name}: 源表 {target_table} 中没有 join 列 {on}")
    return errors


#: 下游按名字取边（标签、校验），所以关系名必须收敛到 canonical 名字。
#: 智能体偶尔会写成 has_icustay / has_clinicalnote，这里按 (from, to) 兜底改名。
CANONICAL_RELATIONS: dict[tuple[str, str], str] = {
    ("Patient", "Encounter"): "has_encounter",
    ("Encounter", "ICUStay"): "has_icu_stay",
    ("Encounter", "Diagnosis"): "has_diagnosis",
    ("Encounter", "Prescription"): "has_prescription",
    ("Encounter", "Procedure"): "has_procedure",
    ("Encounter", "ClinicalNote"): "has_note",
}


def canonical_relation_name(name: str, source: str, target: str) -> str:
    return CANONICAL_RELATIONS.get((source, target), name)


def model_from_proposals(
    objects: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    *,
    version: str = "agent-proposal",
) -> ObjectModel:
    if not objects:
        raise ValueError("提案中没有对象")
    specs: dict[str, ObjectSpec] = {}
    for item in objects:
        name = str(item["name"])
        specs[name] = ObjectSpec(
            name=name,
            keys=tuple(str(key) for key in item["keys"]),
            attributes=tuple(str(attr) for attr in item.get("attributes") or []),
            sensitivity=str(item.get("sensitivity") or "L2"),
            source_table=str(item["source_table"]),
            interface=str(item.get("source_table")),
            concept=str(item.get("reason") or name),
        )

    rel_specs: list[RelationSpec] = []
    for item in relations:
        rel_specs.append(
            RelationSpec(
                name=canonical_relation_name(
                    str(item["name"]), str(item["from"]), str(item["to"])
                ),
                from_object=str(item["from"]),
                to_object=str(item["to"]),
                on=str(item["on"]),
                cardinality=str(item.get("cardinality") or "1:N"),
            )
        )
    return ObjectModel(version=version, objects=specs, relations=tuple(rel_specs))
