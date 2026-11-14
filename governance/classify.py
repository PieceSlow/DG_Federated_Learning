"""阶段 09 · 分类分级。

分级不是贴一个 L1/L2/L3 就结束：每条规则还要说明**为什么这样定级**、**谁可以用**、
**去标识到什么程度**、**访问怎么控**、**能共享到什么边界**。只有这五项写清楚，
级别才是可执行的制度，而不是一个标签。

本模块输出规则表与结果表，结果表把每个数据集/对象落到具体类别与级别。
"""

from __future__ import annotations

import pandas as pd

from governance.context import GovernanceContext, require_state

COLUMNS = [
    "category_id", "category", "object", "level", "basis",
    "risk", "usage_limit", "deid", "access_control", "sharing_boundary",
]

RULES = [
    ["C01", "患者人口学", "Patient", "L3", "可识别性最高（年龄、性别、时间轴）",
     "重识别风险", "仅授权科研", "去标识后仍按敏感处理", "RBAC + 项目审批", "不共享原始行"],
    ["C02", "住院就诊", "Encounter", "L3", "时间与机构信息可辅助重识别",
     "行踪/机构推断", "受控科研", "保留年份偏移", "项目级授权", "仅统计结果"],
    ["C03", "诊断编码", "Diagnosis", "L2", "健康状态，敏感但结构化",
     "病种推断", "授权科研", "编码可保留", "角色+用途", "去标识后可受控共享"],
    ["C04", "药物处方", "Prescription", "L2", "用药可推断病种",
     "用药推断", "授权科研", "药品名/剂量可保留", "角色+用途", "受控共享"],
    ["C05", "医疗操作", "Procedure", "L2", "操作可推断病情",
     "病情推断", "授权科研", "编码可保留", "角色+用途", "受控共享"],
    ["C06", "临床文本", "ClinicalNote", "L3", "自由文本含叙述性隐私",
     "文本泄漏/重识别", "仅受控环境", "不对外提供原文", "沙箱 + 水印", "不共享，仅派生标签"],
    ["C07", "统计汇总", "Patient360/Aggregate", "L1", "聚合后风险低",
     "小样本推断", "可审核开放", "k-匿名/抑制", "披露审查", "满足披露控制后开放"],
]


def _result(ctx: GovernanceContext) -> pd.DataFrame:
    events = ctx.clinical_events
    return pd.DataFrame(
        [
            ["clinical_events", "Diagnosis", "C03", "L2", int((events.event_type == "Diagnosis").sum())],
            ["clinical_events", "Procedure", "C05", "L2", int((events.event_type == "Procedure").sum())],
            ["clinical_events", "Prescription", "C04", "L2", int((events.event_type == "Prescription").sum())],
            ["clinical_events", "ClinicalNote", "C06", "L3", int((events.event_type == "ClinicalNote").sum())],
            ["source_tables", "Encounter", "C02", "L3", int(len(ctx.tables["admissions"]))],
            ["source_tables", "Demographics", "C01", "L3", int(len(ctx.tables["patients"]))],
            ["patient_360", "PatientSummary", "C07", "L1", int(len(ctx.patient_360))],
            ["patient_360", "Demographics", "C01", "L3", int(len(ctx.patient_360))],
        ],
        columns=["dataset_table", "object", "category_id", "level", "rows"],
    )


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")
    rules = pd.DataFrame(RULES, columns=COLUMNS)
    result = _result(ctx)

    dest = ctx.settings.governance / "classification"
    dest.mkdir(parents=True, exist_ok=True)
    rules.to_csv(dest / "rules.csv", index=False, encoding="utf-8-sig")
    result.to_csv(dest / "result.csv", index=False, encoding="utf-8-sig")

    ctx.stats.update(
        {
            "classification_rules": int(len(rules)),
            "classification_results": int(len(result)),
            "levels_in_use": sorted(set(result["level"])),
        }
    )
    ctx.log(
        "分类分级",
        f"{len(rules)} 条分类规则、{len(result)} 条落地结果；"
        f"涉及级别 {', '.join(sorted(set(result['level'])))}。",
    )
