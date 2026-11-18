"""模式匹配：源字段 → 目标字段（对象 schema / MEDS 接口）。

不再使用 33 条静态映射作为 gold；目标字段来自 ``target_schema.yaml``，
候选由四个匹配器生成，经 top-k 选择与约束后输出，覆盖率和人工复核项写入报告。
"""

from governance.alignment.targets import EventTarget, TargetField, load_targets
from governance.alignment.target_schema import (
    ObjectTarget,
    allowed_field_map,
    allowed_table_map,
    load_object_targets,
    target_views,
)
from governance.alignment.schema import FieldView, extract_source_fields
from governance.alignment.select import build_candidates, select_mappings

__all__ = [
    "EventTarget",
    "TargetField",
    "load_targets",
    "ObjectTarget",
    "load_object_targets",
    "target_views",
    "allowed_table_map",
    "allowed_field_map",
    "FieldView",
    "build_candidates",
    "extract_source_fields",
    "select_mappings",
]
