"""阶段 08 · 标签化。

标签不是"再算几个字段"，而是给数据资产建立可检索、可分级、可共享的语义层。本模块定义
四类标签并汇总成标签清单：

* **对象类型**：Diagnosis / Procedure / Prescription / ClinicalNote；
* **术语体系**：ICD9 / ICD10 / MIMIC_FORMULARY / MIMIC_NOTE；
* **语义章节**：ICD 章节（粗粒度病种画像）；
* **敏感级别**：L1 公开 / L2 受控共享 / L3 仅本地。

标签定义集中在这里，避免"同一个患者在不同表里被标成不同级别"。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from governance.context import GovernanceContext, require_state

#: 敏感级别：对象类型 → 级别
SENSITIVITY = {
    "Diagnosis": "L2",
    "Procedure": "L2",
    "Prescription": "L2",
    "ClinicalNote": "L3",
}

ICD10_CHAPTERS = [
    ("A00-B99", "Certain infectious and parasitic diseases", "AB"),
    ("C00-D49", "Neoplasms", "CD"),
    ("E00-E89", "Endocrine, nutritional and metabolic diseases", "E"),
    ("F01-F99", "Mental, behavioural and neurodevelopmental disorders", "F"),
    ("G00-G99", "Diseases of the nervous system", "G"),
    ("H00-H59", "Diseases of the eye and adnexa", "H0"),
    ("H60-H95", "Diseases of the ear and mastoid process", "H6"),
    ("I00-I99", "Diseases of the circulatory system", "I"),
    ("J00-J99", "Diseases of the respiratory system", "J"),
    ("K00-K95", "Diseases of the digestive system", "K"),
    ("L00-L99", "Diseases of the skin and subcutaneous tissue", "L"),
    ("M00-M99", "Diseases of the musculoskeletal system", "M"),
    ("N00-N99", "Diseases of the genitourinary system", "N"),
    ("O00-O9A", "Pregnancy, childbirth and the puerperium", "O"),
    ("P00-P96", "Certain conditions originating in the perinatal period", "P"),
    ("Q00-Q99", "Congenital malformations and chromosomal abnormalities", "Q"),
    ("R00-R99", "Symptoms, signs and abnormal findings", "R"),
    ("S00-T88", "Injury, poisoning and external causes", "ST"),
    ("V00-Y99", "External causes of morbidity", "VY"),
    ("Z00-Z99", "Factors influencing health status", "Z"),
]

ICD9_CHAPTERS = [
    (1, 139, "Infectious and parasitic diseases"),
    (140, 239, "Neoplasms"),
    (240, 279, "Endocrine and metabolic diseases"),
    (280, 289, "Diseases of the blood"),
    (290, 319, "Mental disorders"),
    (320, 389, "Diseases of the nervous system"),
    (390, 459, "Diseases of the circulatory system"),
    (460, 519, "Diseases of the respiratory system"),
    (520, 579, "Diseases of the digestive system"),
    (580, 629, "Diseases of the genitourinary system"),
    (630, 679, "Pregnancy and childbirth"),
    (680, 709, "Diseases of the skin"),
    (710, 739, "Diseases of the musculoskeletal system"),
    (740, 759, "Congenital anomalies"),
    (760, 779, "Perinatal conditions"),
    (780, 799, "Symptoms and signs"),
    (800, 999, "Injury and poisoning"),
]

TAG_DIMENSIONS = {
    "event_type": "对象类型：诊疗事件属于哪一类对象",
    "code_system": "术语体系：编码来自哪套标准",
    "icd_chapter": "语义章节：按 ICD 编码归入的疾病/操作章节",
    "sensitivity": "敏感级别：L1 公开 / L2 受控共享 / L3 仅本地",
    "split": "患者级划分：train / tuning / held_out，防止联邦训练泄漏",
}


def icd_chapter(version: int, code: str) -> str:
    """把 ICD 编码映射到章节标签；无法归类时给明确兜底值而不是空串。"""
    code = str(code).strip().upper()
    if not code:
        return "Unknown"
    if version == 10:
        letter = code[0]
        three = code[:3]
        if letter in "AB":
            return ICD10_CHAPTERS[0][1]
        if letter in "CD":
            return ICD10_CHAPTERS[1][1]
        if three.startswith("H0") or (letter == "H" and len(code) > 1 and code[1].isdigit() and int(code[1]) <= 5):
            return ICD10_CHAPTERS[5][1]
        if letter == "H":
            return ICD10_CHAPTERS[6][1]
        for _, label, letters in ICD10_CHAPTERS[2:]:
            if letter == letters[0]:
                return label
        return "Other / unclassified"
    digits = re.match(r"(\d{1,3})", code)
    if not digits:
        return "Unknown"
    number = int(digits.group(1))
    for low, high, label in ICD9_CHAPTERS:
        if low <= number <= high:
            return label
    return "Other / unclassified"


def sensitivity_for(event_type: str) -> str:
    return SENSITIVITY.get(event_type, "L2")


def _semantic_type_counts(concept_map: pd.DataFrame | None) -> dict[str, int]:
    """按 CUI 统计 UMLS 语义类型（概念数，不是患者数）。"""
    if concept_map is None or concept_map.empty or "sty" not in concept_map.columns:
        return {}
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for cui, sty in zip(concept_map["cui"], concept_map["sty"]):
        cui = str(cui)
        if cui in seen:
            continue
        seen.add(cui)
        for part in str(sty).split("|"):
            part = part.strip()
            if part:
                counts[part] += 1
    return dict(counts.most_common(15))


def _value_counts(series: pd.Series, top: int | None = None) -> dict[str, int]:
    counts = series.fillna("(空)").value_counts()
    if top:
        counts = counts.head(top)
    return {str(k): int(v) for k, v in counts.items()}


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events")
    events = ctx.clinical_events
    labels = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": {
            "event_type": {
                "description": TAG_DIMENSIONS["event_type"],
                "level": "对象类型",
                "values": _value_counts(events["event_type"]),
            },
            "code_system": {
                "description": TAG_DIMENSIONS["code_system"],
                "level": "术语体系",
                "values": _value_counts(events["code_system"]),
            },
            "icd_chapter": {
                "description": TAG_DIMENSIONS["icd_chapter"],
                "level": "语义章节",
                "values": _value_counts(events["icd_chapter"], top=10),
            },
            "sensitivity": {
                "description": TAG_DIMENSIONS["sensitivity"],
                "level": "敏感级别",
                "values": _value_counts(events["sensitivity"]),
                "policy": {
                    "L1": "可公开（数据字典、聚合统计）",
                    "L2": "受控共享（协议 + 审批）",
                    "L3": "仅本地（不共享原始行）",
                },
            },
            "split": {
                "description": TAG_DIMENSIONS["split"],
                "level": "划分",
                "values": _value_counts(events["split"]),
            },
            "semantic_type": {
                "description": "UMLS 语义类型（概念层，按 CUI 统计）",
                "level": "概念语义",
                "values": _semantic_type_counts(ctx.concept_map),
                "note": "UMLS 未启用时为空；计数单位是概念，不是患者",
            },
        },
        "note": "标签描述数据语义与敏感级别，不包含任何患者直接标识。",
    }
    ctx.labels = labels
    (ctx.settings.governance / "metadata" / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.log(
        "标签化",
        "建立 5 个标签维度（对象类型 / 术语体系 / 语义章节 / 敏感级别 / 划分），"
        f"覆盖 {len(events)} 条事件。",
    )
