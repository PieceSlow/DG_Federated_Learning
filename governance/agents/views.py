"""视图层：把本地数据压缩成"可外发"的窄视图。

这是智能体链条的合规边界：**所有送往 LLM 的内容都必须由本模块生成**。
视图只包含结构、聚合统计与候选，不包含行级记录、标识符与临床文本。

``require_no_patient_data`` 是最后一道闸门：任何视图在发出前都会递归检查：
* 字典键命中禁止字段（``subject_id``/``hadm_id``/``note_id``/``text`` 等）；
* 字符串值命中 MIMIC 量级的 8 位标识（``1xxxxxxxx``/``2xxxxxxxx``）。

注意：字段名（如 ``"subject_id"``）作为**取值**出现是允许的——它是元数据；被禁止的是
"以患者标识为键"或"值里出现患者标识"。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

import pandas as pd

#: 不允许作为字典键出现的字段（患者标识、时间戳、临床文本）
FORBIDDEN_KEYS = {
    "subject_id",
    "hadm_id",
    "stay_id",
    "note_id",
    "labevent_id",
    "pharmacy_id",
    "text",
    "text_value",
    "dod",
    "deathtime",
    "admittime",
    "dischtime",
    "intime",
    "outtime",
    "charttime",
    "storetime",
    "starttime",
    "chartdate",
}

#: MIMIC 的 subject_id / hadm_id 量级
_ID_PATTERN = re.compile(r"(?<!\d)[12]\d{7}(?!\d)")


def require_no_patient_data(value: Any, path: str = "payload") -> None:
    """递归校验视图，发现疑似患者数据立即抛错。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"视图包含禁止字段: {path}.{key}")
            require_no_patient_data(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            require_no_patient_data(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and _ID_PATTERN.search(value):
        raise ValueError(f"视图包含疑似患者标识: {path}={value[:40]}")


def digest(value: Any) -> str:
    """稳定的内容摘要，用于 trace 里记录输入输出而不落原文。"""
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def schema_view(tables: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """表结构视图：表名、行数、列名与类型。"""
    return {
        "tables": [
            {
                "table": name,
                "rows": int(len(frame)),
                "columns": [
                    {"name": str(column), "dtype": str(frame[column].dtype)}
                    for column in frame.columns
                ],
            }
            for name, frame in tables.items()
        ]
    }


def stats_view(tables: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """统计视图：行数与各列空值率（只给比率，不给取值）。"""
    return {
        "tables": [
            {
                "table": name,
                "rows": int(len(frame)),
                "null_rates": [
                    {"column": str(column), "rate": round(float(frame[column].isna().mean()), 3)}
                    for column in frame.columns
                ],
            }
            for name, frame in tables.items()
        ]
    }


def field_pair_view(
    source_table: str,
    target_table: str,
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """字段对视图：字段名 + 本地算好的相似度特征（不含取值）。"""
    return {
        "source_table": source_table,
        "target_table": target_table,
        "pairs": pairs,
    }


def graph_stats_view(graph: Mapping[str, Any]) -> dict[str, Any]:
    """图谱统计视图：对象/关系类型与计数，不含实例。"""
    objects = graph.get("objects", {})
    return {
        "objects": [
            {"object": name, "keys": list(spec.get("keys", [])), "rows": int(spec.get("rows", 0))}
            for name, spec in objects.items()
        ],
        "relations": [
            {"from": rel.get("from"), "to": rel.get("to"), "on": rel.get("on")}
            for rel in graph.get("relations", [])
        ],
    }


def requirement_view(text: str) -> dict[str, Any]:
    """业务需求视图：只有需求文字，不含数据。"""
    return {"requirement": text}


def manifest_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """图谱 manifest 视图：对象/关系类型与计数，不含实例。"""
    return {
        "objects": [
            {"object": name, "rows": int(rows)}
            for name, rows in manifest.get("objects", {}).items()
        ],
        "relations": [
            {"relation": name, "rows": int(rows)}
            for name, rows in manifest.get("relations", {}).items()
        ],
    }
