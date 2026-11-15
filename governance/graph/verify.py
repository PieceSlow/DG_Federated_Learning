"""图谱质量校验：让图谱自己发现数据问题。

规则：

| 规则 | 级别 | 含义 |
|---|---|---|
| ``GRAPH_DANGLING_EDGE`` | error | 边的端点不在节点集合中（外键指向了不存在的对象） |
| ``GRAPH_ORPHAN_NODE`` | warning | 主体对象（患者/住院/ICU）没有任何边 |
| ``GRAPH_MISSING_RELATION`` | warning | 事件对象（诊断/处方/操作/文书）没有挂到住院上 |
| ``GRAPH_TEMPORAL_CONFLICT`` | warning | 出院早于入院、出科早于入科、ICU 窗口超出住院窗口 |

这正是课堂笔记里"我们自己做的图谱可以发现原始数据中的质量问题吗"的落点：
图谱不只是展示，它承担了跨表一致性检查。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from governance.graph.build import GraphData

PRIMARY_TYPES = {"Patient", "Encounter", "ICUStay"}
EVENT_TYPES = {"Diagnosis", "Prescription", "Procedure", "ClinicalNote", "Concept"}
TOLERANCE = pd.Timedelta(days=1)


def _attributes(nodes: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in nodes.itertuples():
        try:
            result[row.object_id] = json.loads(row.attributes or "{}")
        except json.JSONDecodeError:
            result[row.object_id] = {}
    return result


def _parse(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    stamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(stamp) else stamp


def _issue(rule: str, severity: str, detail: str, record: str) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "detail": detail, "record": record}


def verify(graph: GraphData) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(rule: str, severity: str, detail: str, record: str) -> None:
        key = (rule, record)
        if key in seen:
            return
        seen.add(key)
        issues.append(_issue(rule, severity, detail, record))

    nodes = graph.nodes
    node_ids = set(nodes["object_id"])
    endpoints: set[str] = set()

    # 1) 悬挂边
    for relation, frame in graph.edges.items():
        for row in frame.itertuples():
            endpoints.add(row.src_id)
            endpoints.add(row.dst_id)
            if row.src_id not in node_ids:
                add("GRAPH_DANGLING_EDGE", "error",
                    f"{relation} 的起点不存在: {row.src_id}", row.src_id)
            if row.dst_id not in node_ids:
                add("GRAPH_DANGLING_EDGE", "error",
                    f"{relation} 的终点不存在: {row.dst_id}", row.dst_id)

    # 2) 孤立节点 / 缺失关系
    for row in nodes.itertuples():
        if row.object_id in endpoints:
            continue
        if row.object_type in PRIMARY_TYPES:
            add("GRAPH_ORPHAN_NODE", "warning",
                f"{row.object_type} 没有任何关系: {row.object_id}", row.object_id)
        elif row.object_type in EVENT_TYPES:
            add("GRAPH_MISSING_RELATION", "warning",
                f"{row.object_type} 未挂到住院上: {row.object_id}", row.object_id)

    # 3) 时间矛盾
    attributes = _attributes(nodes)
    for row in nodes.itertuples():
        if row.object_type != "Encounter":
            continue
        attrs = attributes.get(row.object_id, {})
        admit, discharge = _parse(attrs.get("admittime")), _parse(attrs.get("dischtime"))
        if admit is not None and discharge is not None and discharge < admit:
            add("GRAPH_TEMPORAL_CONFLICT", "warning",
                f"出院时间早于入院时间: {row.object_id}", row.object_id)

    stay_edges = graph.edges.get("has_icu_stay")
    if stay_edges is not None:
        for row in stay_edges.itertuples():
            stay = attributes.get(row.dst_id, {})
            encounter = attributes.get(row.src_id, {})
            intime, outtime = _parse(stay.get("intime")), _parse(stay.get("outtime"))
            admit = _parse(encounter.get("admittime"))
            discharge = _parse(encounter.get("dischtime"))
            if intime is not None and outtime is not None and outtime < intime:
                add("GRAPH_TEMPORAL_CONFLICT", "warning",
                    f"ICU 出科早于入科: {row.dst_id}", row.dst_id)
            if intime is not None and admit is not None and intime < admit - TOLERANCE:
                add("GRAPH_TEMPORAL_CONFLICT", "warning",
                    f"ICU 入科早于入院 1 天以上: {row.dst_id}", row.dst_id)
            if outtime is not None and discharge is not None and outtime > discharge + TOLERANCE:
                add("GRAPH_TEMPORAL_CONFLICT", "warning",
                    f"ICU 出科晚于出院 1 天以上: {row.dst_id}", row.dst_id)

    return issues
