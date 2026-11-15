"""对象级标签：定义、计算与存储。"""

from governance.labels.engine import (
    LabelDefinition,
    compute_labels,
    definitions_catalog,
    load_definitions,
)
from governance.labels.store import ObjectLabelStore

__all__ = [
    "LabelDefinition",
    "ObjectLabelStore",
    "compute_labels",
    "definitions_catalog",
    "load_definitions",
]
