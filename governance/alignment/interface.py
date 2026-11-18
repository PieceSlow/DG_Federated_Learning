"""MEDS 对外接口的落地台账与自检。

这个模块回答两个问题，过去二者都没有答案：

1. **接口的每一列到底由谁提供？** —— ``resolve_interface`` 把
   ``meds_targets.yaml``（接口声明）与 ``field_level.csv``（schema matching 结果）
   对齐，给出 ``resolved_by ∈ {mapping, spec, derived, none, unmapped}``。
   声明里写了 ``uses: [表.列]`` 的，必须能在匹配结果里找到那一行，否则记为
   ``unmapped`` 并进入报告 —— 这就是"接口接上了对齐结果"的证据。
2. **事件流是否符合接口声明？** —— ``check_events`` 校验实际发出的编码命名空间
   是否都被声明、必填列是否真的非空。过去 ``meds_targets.yaml`` 写
   ``DIAGNOSIS.time required`` 而代码一直发 NULL，就是因为没有这一步。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pandas as pd

from governance.alignment.target_schema import allowed_table_map, load_object_targets
from governance.alignment.targets import (
    CORE_FIELDS,
    FIELD_ALIASES,
    EventTarget,
    load_targets,
    source_gaps,
)

RESOLVED_BY = ("mapping", "spec", "derived", "none", "unmapped")

CROSSWALK_COLUMNS = [
    "event_type",
    "code_namespaces",
    "source_tables",
    "target_field",
    "required",
    "declared_source",
    "source_hint",
    "template",
    "matched_source_table",
    "matched_source_field",
    "mapping_method",
    "mapping_status",
    "resolved_by",
    "note",
]


def mapping_index(field_map: pd.DataFrame | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(目标字段, 源表, 源字段) → 匹配行；只收 ``clinical_events`` 表。"""
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    if field_map is None or getattr(field_map, "empty", True):
        return index
    frame = field_map
    if "target_table" in frame.columns:
        frame = frame[frame["target_table"] == "clinical_events"]
    if frame.empty or "target_field" not in frame.columns:
        return index
    for row in frame.itertuples():
        key = (str(row.target_field), str(row.source_table), str(row.source_field))
        candidate = {
            "score": float(getattr(row, "score", 0.0) or 0.0),
            "status": str(getattr(row, "status", "") or ""),
            "method": str(getattr(row, "method", "") or ""),
        }
        if key not in index or candidate["score"] > index[key]["score"]:
            index[key] = candidate
    return index


def _lookup(
    index: dict[tuple[str, str, str], dict[str, Any]],
    target_field: str,
    source_table: str,
    source_field: str,
) -> dict[str, Any] | None:
    return index.get((target_field, source_table, source_field))


@lru_cache(maxsize=1)
def declared_source_tables() -> dict[str, set[str]]:
    """``target_schema.yaml`` 给每个 clinical_events 目标字段声明的源表。

    用它区分两件容易混的事：
    * 模板吃的是**对齐契约内**的列（如 ``prescriptions.formulary_drug_cd → code``）——
      那必须由匹配结果落地，没落地就是 ``unmapped``；
    * 模板吃的是**接口自有**的列（如 ``patients.gender`` 拼 ``GENDER//M``）——
      它不在 clinical_events 的匹配契约里，属于 ``spec``。
    """
    tables = allowed_table_map(load_object_targets())
    return {
        field: set(source_tables)
        for (table, field), source_tables in tables.items()
        if table == "clinical_events"
    }


def resolve_field(
    target: EventTarget,
    field_name: str,
    index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    """单个接口字段的落地情况。"""
    field = target.fields[field_name]
    alias = FIELD_ALIASES[field_name]
    row: dict[str, Any] = {
        "event_type": target.event_type,
        "code_namespaces": "、".join(target.code_namespaces),
        "source_tables": "、".join(target.source_tables),
        "target_field": field_name,
        "required": field.required,
        "declared_source": field.source,
        "source_hint": field.source_hint or "",
        "template": field.template or "",
        "matched_source_table": "",
        "matched_source_field": "",
        "mapping_method": "",
        "mapping_status": "",
        "resolved_by": "none",
        "note": field.note or "",
    }
    if field.source == "none":
        row["resolved_by"] = "none"
        return row

    if field.source == "spec":
        if not field.uses:
            # 常量编码：不来自任何源列
            row["resolved_by"] = "spec"
            return row
        best = None
        for use in field.uses:
            table, _, column = use.partition(".")
            hit = _lookup(index, alias, table, column)
            if hit is not None and (best is None or hit["score"] > best[0]["score"]):
                best = (hit, table, column)
        if best is None:
            declared = declared_source_tables().get(alias, set())
            needs_mapping = any(use.partition(".")[0] in declared for use in field.uses)
            # 契约内的列没落地才是 unmapped；接口自有列（patients.gender 拼 GENDER//M）
            # 不在 clinical_events 的匹配契约里，属于 spec。
            row["resolved_by"] = "unmapped" if needs_mapping else "spec"
            row["note"] = (row["note"] + "；" if row["note"] else "") + (
                f"模板引用的源字段 {', '.join(field.uses)} 在匹配契约内但没有落地"
                if needs_mapping
                else "模板取自接口自有列，不在 clinical_events 的匹配契约内"
            )
            return row
        hit, table, column = best
        row.update(
            matched_source_table=table,
            matched_source_field=column,
            mapping_method=hit["method"],
            mapping_status=hit["status"],
            resolved_by="mapping",
        )
        return row

    # derived / mapping：按声明列去匹配结果里找同一列
    table = field.via or target.table
    column = field.column
    if column:
        hit = _lookup(index, alias, table, column)
        if hit is not None:
            row.update(
                matched_source_table=table,
                matched_source_field=column,
                mapping_method=hit["method"],
                mapping_status=hit["status"],
                resolved_by="mapping",
            )
            return row
    # derived 就是源行直接派生；只有声明 mapping 的字段没落地才算 unmapped
    row["resolved_by"] = "unmapped" if field.source == "mapping" else "derived"
    if field.source == "derived":
        row["note"] = (row["note"] + "；" if row["note"] else "") + (
            "由源行直接派生，不对应 clinical_events 的目标字段"
        )
    else:
        row["note"] = (row["note"] + "；" if row["note"] else "") + "接口要求由匹配提供，但匹配结果里没有这一列"
    return row


def build_crosswalk(
    field_map: pd.DataFrame | None = None,
    targets: dict[str, EventTarget] | None = None,
) -> pd.DataFrame:
    """接口 × 匹配结果 的台账（每事件类型 × 每个核心字段一行）。"""
    targets = targets or load_targets()
    index = mapping_index(field_map)
    rows = [
        resolve_field(target, field_name, index)
        for target in targets.values()
        for field_name in CORE_FIELDS
    ]
    return pd.DataFrame(rows, columns=CROSSWALK_COLUMNS)


def summarize(crosswalk: pd.DataFrame) -> dict[str, Any]:
    counts = crosswalk["resolved_by"].value_counts().to_dict()
    summary = {key: int(counts.get(key, 0)) for key in RESOLVED_BY}
    summary["total"] = int(len(crosswalk))
    return summary


def unmapped(crosswalk: pd.DataFrame) -> list[str]:
    subset = crosswalk[crosswalk["resolved_by"] == "unmapped"]
    return sorted(f"{row.event_type}.{row.target_field}" for row in subset.itertuples())


def review_items(crosswalk: pd.DataFrame) -> list[str]:
    """落到 ``review`` 的接口字段：接口层也要能列出待确认项。"""
    subset = crosswalk[crosswalk["mapping_status"] == "review"]
    return sorted(f"{row.event_type}.{row.target_field}" for row in subset.itertuples())


def _events_frame(events: Any) -> pd.DataFrame | None:
    if events is None:
        return None
    if isinstance(events, pd.DataFrame):
        return events if not events.empty else None
    records = list(events)
    if not records:
        return None
    return pd.DataFrame(records)


def _namespaces(frame: pd.DataFrame) -> dict[str, int]:
    codes = frame["code"].astype(str)
    return {str(key): int(value) for key, value in codes.str.split("//").str[0].value_counts().items()}


def check_events(
    events: Any,
    targets: dict[str, EventTarget] | None = None,
) -> dict[str, Any]:
    """校验实际事件流是否符合接口声明（命名空间 + 必填列）。"""
    targets = targets or load_targets()
    frame = _events_frame(events)
    declared = sorted({name for target in targets.values() for name in target.code_namespaces})
    if frame is None:
        return {
            "declared_namespaces": declared,
            "observed_namespaces": [],
            "undeclared": [],
            "declared_without_events": declared,
            "required": [],
            "events": 0,
        }

    observed = _namespaces(frame)
    required: list[dict[str, Any]] = []
    for event_type, target in targets.items():
        subset = frame[frame["code"].astype(str).str.startswith(
            tuple(f"{name}//" for name in target.code_namespaces)
        )]
        if subset.empty:
            continue
        for field_name, spec in target.fields.items():
            if not spec.required:
                continue
            column = field_name
            nulls = int(subset[column].isna().sum()) if column in subset else int(len(subset))
            required.append(
                {
                    "event_type": event_type,
                    "column": field_name,
                    "rows": int(len(subset)),
                    "null": nulls,
                    "null_rate": round(nulls / len(subset), 4),
                    "source": spec.source,
                }
            )

    return {
        "declared_namespaces": declared,
        "observed_namespaces": sorted(observed),
        "undeclared": sorted(name for name in observed if name not in declared),
        "declared_without_events": sorted(name for name in declared if name not in observed),
        "required": required,
        "required_violations": [item for item in required if item["null"] > 0],
        "events": int(len(frame)),
        "by_namespace": observed,
    }


def interface_report(
    field_map: pd.DataFrame | None,
    events: Any = None,
    targets: dict[str, EventTarget] | None = None,
) -> dict[str, Any]:
    """接口报告：声明 × 匹配结果 × 实际事件流。"""
    targets = targets or load_targets()
    crosswalk = build_crosswalk(field_map, targets)
    return {
        "interface": "MEDS",
        "event_types": len(targets),
        "core_fields": list(CORE_FIELDS),
        "resolution": summarize(crosswalk),
        "by_event": {
            event_type: {
                "resolved_by": {
                    field_name: str(
                        crosswalk[
                            (crosswalk["event_type"] == event_type)
                            & (crosswalk["target_field"] == field_name)
                        ]["resolved_by"].iat[0]
                    )
                    for field_name in CORE_FIELDS
                }
            }
            for event_type in targets
        },
        "unmapped": unmapped(crosswalk),
        "review": review_items(crosswalk),
        "events": check_events(events, targets) if events is not None else None,
        "source_gaps": source_gaps(),
    }
