"""概念对象主键与"事件编码 → UMLS 概念"的解析：图谱与标签共用同一套。

同一份数据里"概念"有两个身份来源：

* UMLS 概念（``cui``）：语义上的一条概念，同义词合并后的唯一标识；
* 编码（``code_system`` + ``code``）：某套术语体系里的一个条目。

图谱与标签如果各写一套主键，两边的 Concept 对象就永远 join 不上。
统一规则 —— **有 CUI 用 CUI，没有 CUI 用编码**：

```text
concept_object_id("ICD10", "I10", "C0018787") -> "Concept:C0018787"
concept_object_id("ICD10", "I10", None)       -> "Concept:ICD10:I10"
```

**解析也必须共用一处**（:class:`ConceptResolver`）。过去图谱按 UMLS 的源体系
（``ICD10PCS`` / ``RXNORM``）查、标签按事件上的 ``code_system``（``ICD10`` /
``MIMIC_FORMULARY``）查，导致操作和处方永远对不上、图谱还丢弃未映射的编码。
现在两头都调 ``resolve(event_type, code_system, code, display)``，
解析不到就退回编码级，图谱与标签的概念键因此从构造上一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_EMPTY = {"", "nan", "none", "null", "<na>"}

#: 事件编码体系 → 概念表里的源体系（UMLS SAB）。按事件类型区分，因为诊断和操作
#: 在 MIMIC 里都叫 ICD9/ICD10，但 ``concept.py`` 写入概念表时：诊断用 ``ICD{v}``、
#: 操作用 ``ICD10PCS`` / ``ICD9CM_PROC``（MIMIC 操作表的 ICD9 行其实是 ICD-9-CM 操作编码）。
_SYSTEM_BY_EVENT = {
    "Diagnosis": {"ICD9": "ICD9", "ICD10": "ICD10"},
    "Procedure": {"ICD9": "ICD9CM_PROC", "ICD10": "ICD10PCS"},
}


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


@dataclass(frozen=True)
class ConceptHit:
    """一条 UMLS 命中。"""

    cui: str
    name: str = ""
    sty: str = ""
    confidence: float = 1.0
    method: str = ""


class ConceptResolver:
    """把事件上的 ``(类型, 编码体系, 编码, 药名)`` 解析成 UMLS 概念。

    ``concept_map`` 为空（没有 UMLS）时，``resolve`` 一律返回 ``None``，调用方退回
    编码级主键——这样无 UMLS 与有 UMLS 走的是同一条代码路径。
    """

    def __init__(self, concept_map: pd.DataFrame | None = None) -> None:
        self._by_code: dict[tuple[str, str], ConceptHit] = {}
        self._by_drug: dict[str, ConceptHit] = {}
        if concept_map is None or getattr(concept_map, "empty", True):
            return
        if "source_system" not in concept_map.columns:
            return
        for row in concept_map.itertuples():
            hit = ConceptHit(
                cui=str(row.cui),
                name=str(getattr(row, "concept_name", "") or ""),
                sty=str(getattr(row, "sty", "") or ""),
                confidence=float(getattr(row, "confidence", 1.0) or 1.0),
                method=str(getattr(row, "method", "") or ""),
            )
            system = str(row.source_system)
            code = str(row.source_code)
            self._by_code.setdefault((system, code.strip()), hit)
            if system == "RXNORM":
                # concept.py 用药品名作为 RXNORM 的 source_code
                self._by_drug.setdefault(code.strip().lower(), hit)

    @property
    def enabled(self) -> bool:
        return bool(self._by_code)

    @staticmethod
    def lookup_system(event_type: str, code_system: str) -> str | None:
        return _SYSTEM_BY_EVENT.get(event_type, {}).get(_clean(code_system))

    def resolve(
        self,
        event_type: str,
        code_system: str,
        code: str,
        display: str | None = None,
    ) -> ConceptHit | None:
        """按编码解析；处方再按药名兜底。解析不到返回 ``None``。"""
        system = self.lookup_system(event_type, code_system)
        if system:
            hit = self._by_code.get((system, _clean(code)))
            if hit is not None:
                return hit
        if event_type == "Prescription" and display:
            return self._by_drug.get(_clean(display).lower())
        return None
