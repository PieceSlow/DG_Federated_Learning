"""阶段 09 · 分类分级。

分级不是贴一个 L1/L2/L3 就结束：每条规则还要说明**为什么这样定级**、**谁可以用**、
**去标识到什么程度**、**访问怎么控**、**能共享到什么边界**。只有这五项写清楚，
级别才是可执行的制度，而不是一个标签。

本模块输出规则表与结果表，结果表把每个数据集/对象落到具体类别与级别。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

from governance.context import GovernanceContext, require_state
from governance.levels import level_of
from governance.review import write_review

RULES_VERSION = "1.0"

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
    ["C07", "统计汇总", "Aggregate", "L1", "聚合后风险低（k≥10）",
     "小样本推断", "可审核开放", "k-匿名/抑制", "披露审查", "满足披露控制后开放"],
    ["C08", "患者级视图", "PatientSummary", "L3", "一人一行，含人口学、诊断与用药摘要",
     "重识别 + 病种推断", "仅授权科研", "去标识后仍按敏感处理", "RBAC + 项目审批", "不共享原始行"],
]


def _event_levels(ctx: GovernanceContext) -> dict[str, str]:
    """从 KV 标签 ``L1.sensitivity`` 读取事件级别；缺标签时回退规则表默认值。"""
    defaults = {
        "Diagnosis": level_of("C03"),
        "Procedure": level_of("C05"),
        "Prescription": level_of("C04"),
        "ClinicalNote": level_of("C06"),
    }
    labels = getattr(ctx, "object_labels", None)
    if labels is None or labels.empty or ctx.clinical_events is None:
        return defaults
    subset = labels[
        (labels["object_type"] == "Event") & (labels["tag_key"] == "L1.sensitivity")
    ].copy()
    if subset.empty:
        return defaults
    subset["record_id"] = subset["object_id"].str.replace("^Event:", "", regex=True)
    merged = ctx.clinical_events[["record_id", "event_type"]].merge(
        subset[["record_id", "tag_value"]], on="record_id", how="left"
    )
    levels: dict[str, str] = {}
    for event_type, group in merged.groupby("event_type"):
        values = group["tag_value"].dropna()
        if not values.empty:
            levels[str(event_type)] = str(values.mode().iat[0])
    return {**defaults, **levels}


def _result(ctx: GovernanceContext) -> pd.DataFrame:
    events = ctx.clinical_events
    levels = _event_levels(ctx)
    return pd.DataFrame(
        [
            ["clinical_events", "Diagnosis", "C03", levels["Diagnosis"], int((events.event_type == "Diagnosis").sum())],
            ["clinical_events", "Procedure", "C05", levels["Procedure"], int((events.event_type == "Procedure").sum())],
            ["clinical_events", "Prescription", "C04", levels["Prescription"], int((events.event_type == "Prescription").sum())],
            ["clinical_events", "ClinicalNote", "C06", levels["ClinicalNote"], int((events.event_type == "ClinicalNote").sum())],
            ["source_tables", "Encounter", "C02", level_of("C02"), int(len(ctx.tables["admissions"]))],
            ["source_tables", "Demographics", "C01", level_of("C01"), int(len(ctx.tables["patients"]))],
            # 患者 360 是"一人一行"的患者级视图，不是聚合统计：按最高级别 L3 落，
            # 与标签（L1.sensitivity）、数据本身一致；C07/L1 只给 k≥10 的聚合统计。
            ["patient_360", "PatientSummary", "C08", level_of("C08"), int(len(ctx.patient_360))],
        ],
        columns=["dataset_table", "object", "category_id", "level", "rows"],
    )


def _rules_hash() -> str:
    payload = json.dumps({"version": RULES_VERSION, "rules": RULES}, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _level_map(result: pd.DataFrame) -> dict[str, str]:
    return {
        f"{row.dataset_table}.{row.object}": str(row.level)
        for row in result.itertuples()
    }


def diff_levels(previous: dict[str, str], current: dict[str, str]) -> dict:
    """两次分级的差异：改了哪些、新增/移除哪些。"""
    return {
        "changed": {
            key: [previous[key], current[key]]
            for key in current
            if key in previous and previous[key] != current[key]
        },
        "added": sorted(set(current) - set(previous)),
        "removed": sorted(set(previous) - set(current)),
    }


def _write_level_history(dest, result: pd.DataFrame) -> dict:
    """追加一行分级历史：时间、规则指纹、各级别、与上次的 diff。

    只追加不覆盖，回答"什么时候、哪条、从什么级别变到什么级别"。
    """
    path = dest / "level_history.jsonl"
    previous: dict[str, str] = {}
    if path.exists():
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            previous = dict(json.loads(lines[-1]).get("levels") or {})
    current = _level_map(result)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "rules_version": RULES_VERSION,
        "rules_hash": _rules_hash(),
        "levels": current,
        "rows": {f"{row.dataset_table}.{row.object}": int(row.rows) for row in result.itertuples()},
        "diff": diff_levels(previous, current),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record["diff"]


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")
    rules = pd.DataFrame(RULES, columns=COLUMNS)
    # 级别以 levels.yaml 为准：规则表里的 level 列只是"目录展示"，不参与执行
    rules["level"] = [level_of(str(item[0])) for item in RULES]
    result = _result(ctx)

    dest = ctx.settings.governance / "classification"
    dest.mkdir(parents=True, exist_ok=True)
    rules.to_csv(dest / "rules.csv", index=False, encoding="utf-8-sig")
    result.to_csv(dest / "result.csv", index=False, encoding="utf-8-sig")
    diff = _write_level_history(dest, result)

    # 待复核信号：新表/新列/疑似自由文本/级别变化；首次运行只建基线
    snapshot_path = ctx.settings.governance / "metadata" / "schema_snapshot.json"
    review = {"count": 0, "signals": []}
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        review = write_review(dest, snapshot, level_diff=diff)

    ctx.stats.update(
        {
            "classification_rules": int(len(rules)),
            "classification_results": int(len(result)),
            "levels_in_use": sorted(set(result["level"])),
        }
    )
    changed = diff["changed"]
    change_note = (
        "；与上次一致"
        if not changed and not diff["added"] and not diff["removed"]
        else "；级别变更 "
        + "、".join(f"{key}: {old}→{new}" for key, (old, new) in changed.items())
        + (f"，新增 {len(diff['added'])} 项" if diff["added"] else "")
    )
    ctx.log(
        "分类分级",
        f"{len(rules)} 条分类规则、{len(result)} 条落地结果；"
        f"涉及级别 {', '.join(sorted(set(result['level'])))}"
        f"{change_note}（历史见 classification/level_history.jsonl）；"
        + (f"待复核 {review['count']} 项（classification/review_required.json）。"
           if review["count"] else "无新增/变更，无需复核。"),
    )
