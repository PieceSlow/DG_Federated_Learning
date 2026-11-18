"""MEDS 接口台账：接口声明 × schema matching 结果。

``field_level_meds.csv`` 现在是**接口消费台账**，不是一张手写对照表：

* 行来自 ``meds_targets.yaml``（接口唯一声明，见 :mod:`governance.alignment.targets`）；
* 每行再用 ``uses`` / ``column`` 去 ``field_level.csv``（匹配结果）里找落地字段，
  回填 ``matched_source_table/field``、``mapping_method``、``mapping_status``、
  ``resolved_by``。``resolved_by`` 分四种：``mapping``（匹配到源字段）、
  ``spec``（常量编码）、``derived``（源行直接派生）、``none``（该事件确实没有这一列），
  以及 ``unmapped``（声明要求映射但匹配结果里没有，必须进报告）。

台账只做接口对照，不产生新目标字段，也不包含 ``n_*`` / ``total_los_days`` 等自造聚合。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from governance.alignment.interface import CROSSWALK_COLUMNS, build_crosswalk
from governance.alignment.targets import CORE_FIELDS, EventTarget, load_targets

COLUMNS = CROSSWALK_COLUMNS


def build_meds_crosswalk(
    target_path: Path | None = None,
    field_map: pd.DataFrame | None = None,
    targets: dict[str, EventTarget] | None = None,
) -> pd.DataFrame:
    """按接口声明 × 匹配结果生成台账（每事件类型 × 5 个核心字段一行）。

    ``field_map`` 为空时仍能生成（``resolved_by`` 全部为 ``spec``/``derived``/``none``/
    ``unmapped``），便于单独检查接口声明是否自洽。
    """
    if targets is None:
        targets = load_targets(target_path)
    return build_crosswalk(field_map, targets)


def validate_crosswalk(frame: pd.DataFrame) -> None:
    """台账自检：覆盖、必填、来源标签，以及"声明映射却没落地"的行必须能报出来。"""
    missing = set(CORE_FIELDS) - set(frame["target_field"])
    if missing:
        raise ValueError(f"MEDS 台账缺少字段: {sorted(missing)}")

    unknown = set(frame["resolved_by"]) - {"mapping", "spec", "derived", "none", "unmapped"}
    if unknown:
        raise ValueError(f"未知的 resolved_by: {sorted(unknown)}")

    for event_type, group in frame.groupby("event_type"):
        if set(group["target_field"]) != set(CORE_FIELDS):
            raise ValueError(f"{event_type} 未覆盖全部 MEDS 核心字段")
        required = set(group.loc[group["required"], "target_field"])
        if not {"subject_id", "code"} <= required:
            raise ValueError(f"{event_type} 的 subject_id/code 必须 required")

    landed = frame[frame["resolved_by"] == "mapping"]
    empty = landed[
        (landed["matched_source_table"].astype(str).str.strip() == "")
        | (landed["matched_source_field"].astype(str).str.strip() == "")
    ]
    if not empty.empty:
        raise ValueError(f"resolved_by=mapping 但没有落地字段: {empty['event_type'].tolist()}")
