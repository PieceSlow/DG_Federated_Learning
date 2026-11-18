"""阶段 10 · 数据目录编制。

五级目录回答五个不同的问题：

| 层级 | 回答的问题 |
|---|---|
| 资源目录 | 数据从哪来、归谁、多久更新 |
| 资产目录 | 治理后有哪些可直接用的数据产品 |
| 服务目录 | 能以什么方式被使用 |
| 共享目录 | 哪些能给合作方、需要什么条件 |
| 开放目录 | 哪些可以公开、依据是什么 |

输出机器可读的 JSON 与可读的 Markdown 两份，便于系统对接和报告引用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from governance.context import GovernanceContext, require_state

LEVELS = [
    ("resource", "资源目录"),
    ("asset", "资产目录"),
    ("service", "服务目录"),
    ("share", "共享目录"),
    ("open", "开放目录"),
]

_LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3}


def _classification_levels(ctx: GovernanceContext) -> dict[str, str]:
    """数据集 → 级别，**只从分级结果读**（`classification/result.csv` 是唯一权威）。

    编目过去各自硬编码级别，和分级结果对不上（患者 360 一边 L1、一边 L3）。
    现在编目不再自己定级：分级结果是唯一来源，编目只引用。
    """
    path = Path(ctx.settings.governance) / "classification" / "result.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    levels: dict[str, set[str]] = {}
    for row in frame.itertuples():
        levels.setdefault(str(row.dataset_table), set()).add(str(row.level))
    return {
        table: "/".join(sorted(found, key=lambda item: _LEVEL_ORDER.get(item, 0)))
        for table, found in levels.items()
    }


def _catalog(ctx: GovernanceContext) -> dict:
    settings = ctx.settings
    events = ctx.clinical_events
    levels = _classification_levels(ctx)
    return {
        "resource": [
            {
                "name": "MIMIC-IV 结构化数据",
                "source": str(settings.mimic_root),
                "level": "L2-L3",
                "owner": "本地数据治理节点",
                "update": "静态快照",
            },
            {
                "name": "MIMIC-IV-Note 出院小结",
                "source": str(settings.note_root / "note"),
                "level": "L3",
                "owner": "本地数据治理节点",
                "update": "静态快照",
            },
        ],
        "asset": [
            {
                "name": "事件仓储 events（MEDS）",
                "count": int(ctx.stats.get("eventstore_rows", 0)),
                "unit": "行",
                "level": levels.get("clinical_events", "L3"),
                "access": "仅本地（含文书原文）",
            },
            {
                "name": "临床事件整合表 clinical_events",
                "count": int(len(events)),
                "unit": "行",
                "level": levels.get("clinical_events", "L2/L3"),
                "access": "授权科研",
            },
            {
                "name": "患者 360 宽表 patient_360",
                "count": int(len(ctx.patient_360)),
                "unit": "行",
                "level": levels.get("patient_360", "L3"),
                "access": "仅本地（一人一行，含诊断/用药摘要）",
            },
            {
                "name": "对象图谱（类型级）object_graph",
                "count": len(ctx.object_graph["objects"]) if ctx.object_graph else 0,
                "unit": "类对象",
                "level": "L1",
                "access": "公开（无患者行）",
            },
            {
                "name": "字段级映射 field_level",
                "count": int(ctx.stats.get("field_mappings", 0)),
                "unit": "条",
                "level": "L1",
                "access": "公开（无患者行）",
            },
            {
                "name": "记录级血缘 record_level",
                "count": int(ctx.stats.get("record_mappings", 0)),
                "unit": "条",
                "level": "L2",
                "access": "仅本地（含 subject_id/hadm_id）",
            },
            {
                "name": "对象标签 object_labels",
                "count": int(len(ctx.object_labels)) if ctx.object_labels is not None else 0,
                "unit": "条",
                "level": "L2/L3",
                "access": "仅本地",
            },
            {
                "name": "分类分级规则与结果",
                "count": int(ctx.stats.get("classification_rules", 0)),
                "unit": "条规则",
                "level": "L1",
                "access": "公开",
            },
        ],
        "service": [
            {"name": "队列筛选服务", "input": "subject_id/split", "level": "L2", "environment": "受控"},
            {"name": "患者 360 查询", "input": "subject_id", "level": "L2", "environment": "受控"},
            {"name": "术语标签查询", "input": "ICD/药品码", "level": "L1", "environment": "公开"},
            {"name": "质量校验服务", "input": "事件流", "level": "L1", "environment": "内部"},
        ],
        "share": [
            {
                "name": "去文本事件表（受控）",
                "scope": "合作机构",
                "level": "L2",
                "condition": "剥离 NOTE* 正文后再共享；数据使用协议 + 伦理审批",
            },
            {
                "name": "患者 360 聚合字段（受控）",
                "scope": "合作机构",
                "level": "L2",
                "condition": "仅 k≥10 的聚合字段，不含患者行与自由文本",
            },
        ],
        "open": [
            {"name": "数据字典/字段说明", "scope": "公开", "level": "L1", "condition": "无"},
            {"name": "对象图谱与分级规则", "scope": "公开", "level": "L1", "condition": "无患者行"},
            {"name": "统计汇总（k≥10）", "scope": "公开", "level": "L1", "condition": "披露审查"},
        ],
    }


def _markdown(catalog: dict) -> str:
    lines = ["# 数据目录（五级）", ""]
    for key, title in LEVELS:
        lines += [f"## {title}", "", "| 名称 | 说明 | 级别 |", "|---|---|---|"]
        for item in catalog[key]:
            desc = (
                item.get("source")
                or item.get("access")
                or item.get("environment")
                or item.get("condition")
                or ""
            )
            if item.get("count") is not None:
                desc = f"{desc}；{item['count']} {item.get('unit', '条')}"
            lines.append(f"| {item['name']} | {desc} | {item['level']} |")
        lines.append("")
    return "\n".join(lines)


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")
    catalog = _catalog(ctx)
    ctx.catalog = catalog

    dest = ctx.settings.governance / "catalog"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "data_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / "data_catalog.md").write_text(_markdown(catalog), encoding="utf-8")
    ctx.log(
        "编目",
        "建立五级数据目录："
        + "、".join(f"{title} {len(catalog[key])} 项" for key, title in LEVELS)
        + "。",
    )
