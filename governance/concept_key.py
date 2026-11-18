"""概念对象主键：图谱与标签共用同一套命名。

同一份数据里"概念"有两个身份来源：

* UMLS 概念（``cui``）：语义上的一条概念，同义词合并后的唯一标识；
* 编码（``code_system`` + ``code``）：某套术语体系里的一个条目。

图谱与标签如果各写一套主键，两边的 Concept 对象就永远 join 不上。
这里统一成一条规则 —— **有 CUI 用 CUI，没有 CUI 用编码**：

```text
concept_object_id("ICD10", "I10", "C0018787") -> "Concept:C0018787"
concept_object_id("ICD10", "I10", None)       -> "Concept:ICD10:I10"
```

无 UMLS 时（本机当前状态）两边都退化为编码键，彼此仍然对得上。
"""

from __future__ import annotations

_EMPTY = {"", "nan", "none", "null", "<na>"}


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def concept_object_id(code_system: object, code: object, cui: object = None) -> str:
    """返回概念对象的主键，图谱节点与标签行必须用同一个函数生成。"""
    identifier = _clean(cui)
    if identifier and identifier.lower() not in _EMPTY:
        return f"Concept:{identifier}"
    return f"Concept:{_clean(code_system)}:{_clean(code)}"


def has_concept_cui(cui: object) -> bool:
    identifier = _clean(cui)
    return bool(identifier) and identifier.lower() not in _EMPTY
