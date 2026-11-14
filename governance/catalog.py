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

from governance.context import GovernanceContext, require_state

LEVELS = [
    ("resource", "资源目录"),
    ("asset", "资产目录"),
    ("service", "服务目录"),
    ("share", "共享目录"),
    ("open", "开放目录"),
]


def _catalog(ctx: GovernanceContext) -> dict:
    settings = ctx.settings
    events = ctx.clinical_events
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
                "rows": int(ctx.stats.get("eventstore_rows", 0)),
                "level": "L2/L3",
                "access": "仅本地",
            },
            {
                "name": "临床事件整合表 clinical_events",
                "rows": int(len(events)),
                "level": "L2/L3",
                "access": "授权科研",
            },
            {
                "name": "患者 360 宽表 patient_360",
                "rows": int(len(ctx.patient_360)),
                "level": "L1/L2",
                "access": "授权科研",
            },
            {
                "name": "对象图谱 object_graph",
                "rows": len(ctx.object_graph["objects"]) if ctx.object_graph else 0,
                "level": "L1",
                "access": "公开（无患者行）",
            },
            {
                "name": "字段级/记录级映射",
                "rows": int(ctx.stats.get("field_mappings", 0)) + int(ctx.stats.get("record_mappings", 0)),
                "level": "L1",
                "access": "公开（无患者行）",
            },
            {
                "name": "分类分级规则与结果",
                "rows": int(ctx.stats.get("classification_rules", 0)),
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
            {"name": "去标识事件表（受控）", "scope": "合作机构", "level": "L2", "condition": "数据使用协议 + 伦理审批"},
            {"name": "患者 360 汇总（受控）", "scope": "合作机构", "level": "L2", "condition": "仅汇总字段"},
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
