"""阶段 06 · 字段对齐与血缘。

"对齐"不只是把字段拼到一张表里，而是要把**每个字段的来历、变换、口径**写清楚，
否则数据资产无法被审计、也无法在不同中心之间核对。

两级映射：

* **字段级**（``field_level.csv``）：数据集字段 → 源表/源字段/转换规则/类型/缺失说明；
* **记录级**（``record_level.parquet``）：每条数据集记录 → 源表 + 源记录 ID。

聚合行（患者 360）无法一对一落到单条源记录，用 ``derived:patient_360:<subject_id>``
表示 subject 级血缘——不伪造一对一。
"""

from __future__ import annotations

import pandas as pd

from governance.context import GovernanceContext, require_state


def _field(table: str, field: str, source_table: str, source_field: str,
           transform: str, dtype: str, note: str) -> dict[str, str]:
    return {
        "dataset_table": table,
        "dataset_field": field,
        "source_table": source_table,
        "source_field": source_field,
        "transform": transform,
        "dtype": dtype,
        "note": note,
    }


def _field_map() -> pd.DataFrame:
    rows = [
        _field("clinical_events", "record_id", "derived", "-", "按对象前缀生成：DX/PX/RX/NOTE", "string", "同一源行稳定唯一"),
        _field("clinical_events", "subject_id", "patients", "subject_id", "直接引用", "int64", "全表非空，外键到 Patient"),
        _field("clinical_events", "hadm_id", "admissions", "hadm_id", "直接引用", "int64", "门诊检验等可为空；本表事件均来自住院表"),
        _field("clinical_events", "stay_id", "icustays", "stay_id", "按事件时间落入 [intime, outtime] 挂接", "int64", "无 ICU 时间窗时不挂接，保留空值"),
        _field("clinical_events", "event_type", "derived", "-", "按来源表映射 Diagnosis/Procedure/Prescription/ClinicalNote", "string", "对象类别标签"),
        _field("clinical_events", "event_time", "多个源表", "admittime/starttime/chartdate/charttime", "统一转 timestamp", "timestamp", "诊断无时间，保持空值不臆测"),
        _field("clinical_events", "code_system", "derived", "-", "ICD9/ICD10/MIMIC_FORMULARY/MIMIC_NOTE", "string", "术语体系标签"),
        _field("clinical_events", "code", "diagnoses_icd/procedures_icd/prescriptions", "icd_code/formulary_drug_cd", "去空格；不做术语归一", "string", "原样保留编码"),
        _field("clinical_events", "display", "d_icd_diagnoses/d_icd_procedures/prescriptions", "long_title/drug", "字典连接", "string", "术语可读标签"),
        _field("clinical_events", "numeric_value", "prescriptions", "dose_val_rx", "to_numeric", "float64", "非数值剂量（如 1-2）为空"),
        _field("clinical_events", "text_value", "prescriptions/discharge", "dose_val_rx+dose_unit_rx+route / text", "拼接或原文", "string", "临床文本仅本地"),
        _field("clinical_events", "icd_chapter", "derived", "-", "按 ICD 编码首字母/数字段映射章节", "string", "医学语义的粗粒度标签"),
        _field("clinical_events", "source_table", "derived", "-", "记录来源表", "string", "记录级血缘"),
        _field("clinical_events", "source_record_id", "derived", "-", "表名:行号 / discharge:note_id", "string", "可回溯到原始行"),
        _field("clinical_events", "split", "事件仓储 subject_splits", "split", "按 subject_id 连接", "string", "患者级划分，防止联邦训练泄漏"),
        _field("clinical_events", "sensitivity", "derived", "-", "按对象类型赋 L2/L3", "string", "分类分级标签"),
        _field("patient_360", "subject_id", "patients", "subject_id", "直接引用", "int64", "主键"),
        _field("patient_360", "gender", "patients", "gender", "直接引用", "string", "人口学"),
        _field("patient_360", "anchor_age", "patients", "anchor_age", "直接引用", "int64", "MIMIC 锚定年龄"),
        _field("patient_360", "anchor_year", "patients", "anchor_year", "直接引用", "int64", "去标识时间轴"),
        _field("patient_360", "n_admissions", "admissions", "hadm_id", "count", "int64", "聚合"),
        _field("patient_360", "n_icu", "icustays", "stay_id", "count", "int64", "聚合"),
        _field("patient_360", "total_los_days", "admissions", "admittime/dischtime", "sum(dischtime-admittime)", "float64", "聚合"),
        _field("patient_360", "mortality", "admissions", "hospital_expire_flag", "max", "int64", "聚合"),
        _field("patient_360", "n_diagnosis", "diagnoses_icd", "icd_code", "count", "int64", "聚合"),
        _field("patient_360", "n_procedure", "procedures_icd", "icd_code", "count", "int64", "聚合"),
        _field("patient_360", "n_prescription", "prescriptions", "pharmacy_id", "count", "int64", "聚合"),
        _field("patient_360", "n_notes", "discharge", "note_id", "count", "int64", "聚合"),
        _field("patient_360", "top_diagnosis", "diagnoses_icd + d_icd_diagnoses", "icd_code/long_title", "出现次数最多的诊断", "string", "聚合标签"),
        _field("patient_360", "top_drug", "prescriptions", "drug", "出现次数最多的药品", "string", "聚合标签"),
        _field("patient_360", "text_chars", "discharge", "text", "sum(len(text))", "int64", "文本规模"),
        _field("patient_360", "split", "事件仓储 subject_splits", "split", "按 subject_id 连接", "string", "患者级划分"),
        _field("patient_360", "sensitivity", "derived", "-", "行级取最高级别 L3（含人口学）", "string", "分类分级标签"),
    ]
    return pd.DataFrame(rows)


def _record_map(events: pd.DataFrame, p360: pd.DataFrame) -> pd.DataFrame:
    record_map = events[
        ["record_id", "subject_id", "hadm_id", "stay_id", "source_table",
         "source_record_id", "event_type", "split"]
    ].copy()
    record_map["dataset_table"] = "clinical_events"

    p360_map = p360[["subject_id", "split"]].copy()
    p360_map["record_id"] = "P360:" + p360_map["subject_id"].astype(str)
    p360_map["hadm_id"] = None
    p360_map["stay_id"] = None
    p360_map["source_table"] = "patients"
    p360_map["event_type"] = "PatientSummary"
    p360_map["dataset_table"] = "patient_360"
    p360_map["source_record_id"] = "derived:patient_360:" + p360_map["subject_id"].astype(str)
    return pd.concat([record_map, p360_map[record_map.columns]], ignore_index=True)


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")
    field_map = _field_map()
    record_map = _record_map(ctx.clinical_events, ctx.patient_360)

    ctx.field_map = field_map
    ctx.record_map = record_map

    dest = ctx.settings.governance / "mapping"
    dest.mkdir(parents=True, exist_ok=True)
    field_map.to_csv(dest / "field_level.csv", index=False, encoding="utf-8-sig")
    typed = record_map.copy()
    typed["hadm_id"] = pd.array(typed["hadm_id"], dtype="Int64")
    typed["stay_id"] = pd.array(typed["stay_id"], dtype="Int64")
    typed.to_parquet(dest / "record_level.parquet", index=False)

    ctx.stats.update(
        {
            "field_mappings": int(len(field_map)),
            "record_mappings": int(len(record_map)),
        }
    )
    ctx.log(
        "字段对齐",
        f"字段级映射 {len(field_map)} 条、记录级映射 {len(record_map)} 条；"
        "聚合行使用 subject 级血缘，不伪造一对一来源。",
    )
