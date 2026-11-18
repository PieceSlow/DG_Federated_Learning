"""MEDS 字段交叉表：源字段 → MEDS 核心 5 字段。

与 ``field_level.csv``（schema matching 的匹配结果）并存：
* ``field_level.csv`` 回答"这个数据集字段从哪个源字段来、怎么变换"；
* ``field_level_meds.csv`` 回答"MEDS 的每类事件、每个核心字段由哪些源字段提供"。

交叉表只做接口对照，不产生新目标字段，也不包含 ``n_*`` / ``total_los_days`` 等自造聚合。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from governance.alignment.targets import CORE_FIELDS, load_targets

COLUMNS = [
    "event_type",
    "source_tables",
    "target_field",
    "required",
    "template",
    "source_hint",
    "note",
    "transform",
]

_TRANSFORMS = {
    "subject_id": "直接引用，转为 int64",
    "time": "源时间字段解析为 timestamp；无时间字段时保持空值",
    "code": "按模板拼接命名空间//值；编码原样保留，不做术语归一",
    "numeric_value": "数值字段转 float32；非数值剂量为空",
    "text_value": "文本/描述字段原样写入（仅本地）",
}


def build_meds_crosswalk(targets_path: Path | None = None) -> pd.DataFrame:
    """按 ``meds_targets.yaml`` 生成 9 类事件 × 5 个核心字段的交叉表。"""
    targets = load_targets(targets_path)
    rows: list[dict[str, object]] = []
    for event_type, target in targets.items():
        for field in CORE_FIELDS:
            spec = target.fields[field]
            rows.append(
                {
                    "event_type": event_type,
                    "source_tables": "、".join(target.source_tables),
                    "target_field": field,
                    "required": bool(spec.required),
                    "template": spec.template or "",
                    "source_hint": spec.source_hint or "",
                    "note": spec.note or "",
                    "transform": _TRANSFORMS.get(field, "直接引用"),
                }
            )
    return pd.DataFrame(rows, columns=COLUMNS)


def validate_crosswalk(frame: pd.DataFrame) -> None:
    """交叉表自检：每个事件类型必须覆盖 5 个核心字段，且 ``subject_id``/``code`` 必填。"""
    missing = set(CORE_FIELDS) - set(frame["target_field"])
    if missing:
        raise ValueError(f"MEDS 交叉表缺少字段: {sorted(missing)}")
    for event_type, group in frame.groupby("event_type"):
        if set(group["target_field"]) != set(CORE_FIELDS):
            raise ValueError(f"{event_type} 未覆盖全部 MEDS 核心字段")
        required = set(group.loc[group["required"], "target_field"])
        if not {"subject_id", "code"} <= required:
            raise ValueError(f"{event_type} 的 subject_id/code 必须 required")
