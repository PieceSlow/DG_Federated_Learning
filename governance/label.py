"""阶段 09 · 标签化（KV 对象标签）。

标签不是"再算几个字段"，而是打在数据对象上的 KV 断言：
``object_type + object_id + tag_key + tag_value``。本阶段同时输出：

* ``labels/object_labels.parquet``：对象标签长表（L1–L4）；
* ``labels/tag_dictionary.csv``：标签字典（定义、层、口径、来源）；
* ``metadata/labels.json``：字段统计 + 标签体系汇总 + 覆盖率。

"医学标签强化"只对应 L3 概念级（``L3.cui`` / ``L3.semantic_type``），由 UMLS
``concept_map`` 提供；没有 UMLS 时 ``L3.semantic_type`` 降级为 ICD 章节，不生成 CUI。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from governance.context import GovernanceContext, require_state
from governance.labels import (
    ObjectLabelStore,
    compute_labels,
    definitions_catalog,
    load_definitions,
)
from governance.text_notes import note_frame

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

#: 字段统计维度：这些是列/元数据，不是 KV 标签。
FIELD_STATISTICS = {
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


def _coverage(object_labels: pd.DataFrame, events: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> dict[str, dict]:
    def covered(object_type: str) -> set[str]:
        subset = object_labels[object_labels["object_type"] == object_type]
        return set(subset["object_id"])

    event_ids = {"Event:" + str(value) for value in events["record_id"]}
    patient_ids = {"Patient:" + str(value) for value in tables["patients"]["subject_id"]}
    concept_ids = {
        f"Concept:{system}:{code}"
        for system, code in events[["code_system", "code"]].dropna().drop_duplicates().itertuples(index=False)
    }
    return {
        "Event": {
            "total": len(event_ids),
            "labeled": len(covered("Event") & event_ids),
            "ratio": round(len(covered("Event") & event_ids) / len(event_ids), 4) if event_ids else 0.0,
        },
        "Patient": {
            "total": len(patient_ids),
            "labeled": len(covered("Patient") & patient_ids),
            "ratio": round(len(covered("Patient") & patient_ids) / len(patient_ids), 4) if patient_ids else 0.0,
        },
        "Concept": {
            "total": len(concept_ids),
            "labeled": len(covered("Concept") & concept_ids),
            "ratio": round(len(covered("Concept") & concept_ids) / len(concept_ids), 4) if concept_ids else 0.0,
        },
    }


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events")
    events = ctx.clinical_events

    version, definitions = load_definitions()
    object_labels = compute_labels(
        ctx.tables,
        notes=note_frame(ctx),
        events=events,
        concept_map=getattr(ctx, "concept_map", None),
        manual_path=ctx.settings.manual_labels,
        definitions=definitions,
        version=version,
    )
    ObjectLabelStore(ctx.settings.governance / "labels").write(object_labels)

    counts = (
        {str(k): int(v) for k, v in object_labels.groupby("tag_key").size().items()}
        if not object_labels.empty
        else {}
    )
    layer_counts = (
        {str(k): int(v) for k, v in object_labels.groupby("layer").size().items()}
        if not object_labels.empty
        else {}
    )

    dictionary = pd.DataFrame(definitions_catalog(definitions))
    dictionary["count"] = dictionary["tag_key"].map(counts).fillna(0).astype(int)
    labels_dir = ctx.settings.governance / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    dictionary.to_csv(labels_dir / "tag_dictionary.csv", index=False, encoding="utf-8-sig")

    coverage = _coverage(object_labels, events, ctx.tables)
    labels = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label_system": {
            "version": version,
            "note": "标签 = 打在对象上的 KV；L1 资产 / L2 记录 / L3 概念 / L4 断言",
            "layers": layer_counts,
            "tag_keys": counts,
        },
        "coverage": coverage,
        "concept": {
            "umls_enabled": getattr(ctx, "concept_map", None) is not None,
            "semantic_type_counts": _semantic_type_counts(getattr(ctx, "concept_map", None)),
            "note": "UMLS 未启用时 L3.semantic_type 降级为 ICD 章节，不生成 L3.cui",
        },
        "field_statistics": {
            key: {
                "description": description,
                "values": _value_counts(
                    events[key], top=10 if key == "icd_chapter" else None
                ),
            }
            for key, description in FIELD_STATISTICS.items()
            if key in events
        },
        "object_labels": {
            "version": version,
            "definitions": definitions_catalog(definitions),
            "counts": counts,
            "manual_entries": int((object_labels["source"] == "manual").sum())
            if not object_labels.empty
            else 0,
            "note": "标签打在对象上（object_type/object_id），tag_value 为字符串",
        },
    }

    ctx.object_labels = object_labels
    ctx.stats["object_labels"] = {
        "rows": int(len(object_labels)),
        "keys": counts,
        "layers": layer_counts,
        "manual": labels["object_labels"]["manual_entries"],
        "coverage": coverage,
    }

    ctx.labels = labels
    (ctx.settings.governance / "metadata" / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ctx.settings.governance / "quality" / "label_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.log(
        "标签化",
        f"KV 标签 {len(object_labels)} 条，覆盖 {len(counts)} 个 tag_key；"
        f"层分布 {layer_counts}；概念覆盖率 {coverage['Concept']['ratio']:.1%}。",
    )
