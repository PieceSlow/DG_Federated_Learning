"""模式匹配：把源表字段与目标数据集字段对齐。

四个匹配器各自出分，加权组合后按目标字段选最优源字段；现有 33 条人工映射保留为 gold，
用于计算 precision/recall——不静默替换，差异逐条可见。
"""

from governance.alignment.targets import EventTarget, TargetField, load_targets
from governance.alignment.schema import FieldView, extract_source_fields, targets_from_gold
from governance.alignment.select import build_candidates, select_mappings

__all__ = [
    "EventTarget",
    "TargetField",
    "load_targets",
    "FieldView",
    "build_candidates",
    "extract_source_fields",
    "select_mappings",
    "targets_from_gold",
]
