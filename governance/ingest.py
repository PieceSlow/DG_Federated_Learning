"""阶段 01 · 数据接入。

做三件事：

1. **定队列**：取 ``icustays`` 中 ``subject_id`` 最小的 N 名患者，保证确定性、可复现；
2. **按对象取全量历史**：大表分块扫描，只保留队列行，不截断患者历史；
3. **登记数据源清单**：粒度、主键、内容、敏感级别、读取方式，落成资产。

大表扫描用"排序探测 + 提前停止"：只要观察到 ``subject_id`` 非递减，且已读到超过队列
最大 ID，就停止；一旦发现乱序就继续扫到 EOF，保证不丢行。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from governance.config import resolve_data_file
from governance.context import GovernanceContext


def scan_for_subjects(
    path: Path,
    usecols: list[str],
    subjects: set[int],
    chunksize: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """分块扫描一张按 ``subject_id`` 排序的大表，只保留队列患者的行。"""
    parts: list[pd.DataFrame] = []
    prev_max = -1
    sorted_ok = True
    scanned = 0
    early = False
    max_subject = max(subjects)
    reader = pd.read_csv(path, usecols=usecols, chunksize=chunksize)
    for chunk in reader:
        series = pd.to_numeric(chunk["subject_id"], errors="coerce")
        smin = series.min()
        if sorted_ok and pd.notna(smin) and smin < prev_max:
            sorted_ok = False
        if pd.notna(series.max()):
            prev_max = max(prev_max, int(series.max()))
        hit = chunk[series.isin(subjects)]
        if not hit.empty:
            parts.append(hit)
        scanned += len(chunk)
        if sorted_ok and pd.notna(smin) and int(smin) > max_subject:
            early = True
            break
    frame = pd.concat(parts, ignore_index=False) if parts else pd.DataFrame(columns=usecols)
    return frame, {
        "file": path.name,
        "rows_scanned": scanned,
        "rows_kept": len(frame),
        "early_stop": early,
        "sorted_by_subject_id": sorted_ok,
    }


def _filter_subjects(frame: pd.DataFrame, subjects: set[int]) -> pd.DataFrame:
    return frame[frame["subject_id"].isin(subjects)].copy()


#: 各源表的时间列；接入时统一成 datetime，避免"口径靠下游各自解析"
TIME_COLUMNS = {
    "patients": ("dod",),
    "admissions": ("admittime", "dischtime", "deathtime", "edregtime", "edouttime"),
    "icustays": ("intime", "outtime"),
    "procedures_icd": ("chartdate",),
    "prescriptions": ("starttime", "stoptime"),
    "labevents": ("charttime", "storetime"),
}


def coerce_times(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """把时间列统一成 datetime（就地修改）。

    字段对齐的类型匹配依赖 dtype：若 ``procedures_icd.chartdate`` 是字符串、
    而目标 ``event_time`` 是 datetime，类型匹配器会判为不兼容，正确的字段映射
    反而被类型约束挡掉。源表统一 dtype，匹配结果才可信。
    """
    for column in TIME_COLUMNS.get(name, ()):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def run(ctx: GovernanceContext) -> None:
    settings = ctx.settings
    mimic_root = settings.mimic_root

    icu_all = pd.read_csv(resolve_data_file(mimic_root, "icu", "icustays"))
    subjects = sorted(
        int(x) for x in pd.to_numeric(icu_all["subject_id"], errors="coerce").dropna().unique()
    )[: settings.cohort_size]
    subject_set = set(subjects)

    patients = _filter_subjects(pd.read_csv(resolve_data_file(mimic_root, "hosp", "patients")), subject_set)
    admissions = _filter_subjects(
        pd.read_csv(resolve_data_file(mimic_root, "hosp", "admissions")), subject_set
    )
    icustays = icu_all[icu_all["subject_id"].isin(subject_set)].copy()
    diagnoses = _filter_subjects(
        pd.read_csv(resolve_data_file(mimic_root, "hosp", "diagnoses_icd")), subject_set
    )
    procedures = _filter_subjects(
        pd.read_csv(resolve_data_file(mimic_root, "hosp", "procedures_icd")), subject_set
    )

    prescriptions, pres_scan = scan_for_subjects(
        resolve_data_file(mimic_root, "hosp", "prescriptions"),
        [
            "subject_id",
            "hadm_id",
            "pharmacy_id",
            "starttime",
            "drug",
            "drug_type",
            "dose_val_rx",
            "dose_unit_rx",
            "route",
            "formulary_drug_cd",
        ],
        subject_set,
        chunksize=200_000,
    )

    labevents, lab_scan = scan_for_subjects(
        resolve_data_file(mimic_root, "hosp", "labevents"),
        [
            "labevent_id",
            "subject_id",
            "hadm_id",
            "itemid",
            "charttime",
            "valuenum",
            "valueuom",
            "flag",
        ],
        subject_set,
        chunksize=500_000,
    )

    tables = {
        "patients": patients,
        "admissions": admissions,
        "icustays": icustays,
        "diagnoses_icd": diagnoses,
        "procedures_icd": procedures,
        "prescriptions": prescriptions,
        "labevents": labevents,
        "d_labitems": pd.read_csv(resolve_data_file(mimic_root, "hosp", "d_labitems")),
        "d_icd_diagnoses": pd.read_csv(resolve_data_file(mimic_root, "hosp", "d_icd_diagnoses")),
        "d_icd_procedures": pd.read_csv(resolve_data_file(mimic_root, "hosp", "d_icd_procedures")),
    }
    for name in TIME_COLUMNS:
        if name in tables:
            coerce_times(name, tables[name])

    ctx.tables = tables
    # 表结构快照：给"动态新增对象"用（不依赖再次读原始数据）
    schema_snapshot = {
        name: {
            "rows": int(len(frame)),
            "columns": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        }
        for name, frame in tables.items()
    }
    metadata_dir = ctx.settings.governance / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "schema_snapshot.json").write_text(
        json.dumps(schema_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.cohort = subjects
    ctx.stats.update(
        {
            "source_dataset": "MIMIC-IV v3.1",
            "cohort_selection": f"icustays 中 subject_id 最小的前 {len(subjects)} 名患者",
            "cohort_size": len(subjects),
            "admissions": len(admissions),
            "icu_stays": len(icustays),
            "prescriptions_rows": len(prescriptions),
            "labevents_rows": len(labevents),
            "scans": {"prescriptions": pres_scan, "labevents": lab_scan},
        }
    )

    ctx.inventory = [
        _inventory_row("patients", "hosp", "1 行/患者", "subject_id", "人口学", "L3",
                       len(patients), "full", "Patient 对象"),
        _inventory_row("admissions", "hosp", "1 行/住院", "hadm_id", "住院时间、类型、死亡标志", "L3",
                       len(admissions), "full", "Encounter 对象"),
        _inventory_row("icustays", "icu", "1 行/ICU 停留", "stay_id", "ICU 入出科、护理单元", "L3",
                       len(icustays), "full", "ICUStay 对象"),
        _inventory_row("diagnoses_icd", "hosp", "1 行/诊断", "subject_id+hadm_id+seq_num", "ICD 诊断", "L2",
                       len(diagnoses), "full", "Diagnosis 对象与标签"),
        _inventory_row("procedures_icd", "hosp", "1 行/操作", "subject_id+hadm_id+seq_num", "ICD 操作", "L2",
                       len(procedures), "full", "Procedure 对象与标签"),
        _inventory_row("prescriptions", "hosp", "1 行/处方", "pharmacy_id+行号", "药品、剂量、途径", "L2",
                       len(prescriptions), "stream", "Prescription 对象与标签"),
        _inventory_row("labevents", "hosp", "1 行/检验", "labevent_id", "检验项目与数值", "L2",
                       len(labevents), "stream", "MEDS 检验事件"),
        _inventory_row("d_labitems", "hosp", "1 行/检验项目码", "itemid", "检验项目字典", "L1",
                       len(tables["d_labitems"]), "full", "编码描述"),
        _inventory_row("d_icd_diagnoses", "hosp", "1 行/ICD 诊断码", "icd_code+icd_version", "诊断码字典", "L1",
                       len(tables["d_icd_diagnoses"]), "full", "编码描述与章节标签"),
        _inventory_row("d_icd_procedures", "hosp", "1 行/ICD 操作码", "icd_code+icd_version", "操作码字典", "L1",
                       len(tables["d_icd_procedures"]), "full", "编码描述与章节标签"),
        _inventory_row("discharge", "note", "1 行/出院小结", "note_id", "临床文本", "L3",
                       None, "stream", "ClinicalNote 对象与文本事件"),
    ]
    inventory = pd.DataFrame(ctx.inventory)
    # discharge 的读取行数要等文本阶段才知道，这里留空；转 Int64 避免整列变成 100.0
    inventory["rows_loaded"] = inventory["rows_loaded"].astype("Int64")
    inventory.to_csv(
        settings.governance / "metadata" / "data_source_inventory.csv",
        index=False,
        encoding="utf-8-sig",
    )

    ctx.log(
        "数据接入",
        f"按 subject_id 升序选取 {len(subjects)} 名 ICU 患者；读取 11 张源表"
        f"（含 3 张字典表），大表采用分块扫描 + 排序探测提前停止。",
    )


def _inventory_row(
    name: str,
    group: str,
    grain: str,
    primary_key: str,
    content: str,
    sensitivity: str,
    rows: int | None,
    read_mode: str,
    used_for: str,
) -> dict[str, Any]:
    return {
        "file": name,
        "group": group,
        "grain": grain,
        "primary_key": primary_key,
        "content": content,
        "sensitivity": sensitivity,
        "rows_loaded": rows,
        "read_mode": read_mode,
        "used_for": used_for,
    }
