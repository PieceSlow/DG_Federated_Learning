"""阶段 02 · 组织与标准化：把源表组织成对象与事件流。

三个子步骤：

1. **对象索引**：先把 Patient / Encounter / ICUStay 的键与时间窗整理出来，
   后续所有事件都要挂到这些对象上；
2. **跨表关联**：住院↔患者、住院↔ICU，并校验入院/出院、入科/出科时间；
3. **事件映射**：人口学、住院、ICU、诊断、检验、处方 → ``命名空间//值`` 编码事件；
   出院小结 → 原文 + 章节/诊断/用药事件（确定性规则）。

对象模型本身在 :mod:`governance.objectify` 里固化为治理资产；本模块只构建事件映射
所需的对象索引。
"""

from __future__ import annotations

import re
import time

import pandas as pd

from governance.config import resolve_data_file
from governance.context import GovernanceContext
from governance.ingest import scan_for_subjects
from governance.meds import MAPPING_VERSION
from governance.text_extraction import extract_note_events, split_sections


def _index_objects(ctx: GovernanceContext) -> None:
    tables = ctx.tables
    patients = tables["patients"].copy()
    encounters = tables["admissions"].copy()
    icu = tables["icustays"].copy()
    encounters["admittime"] = pd.to_datetime(encounters["admittime"], errors="coerce")
    encounters["dischtime"] = pd.to_datetime(encounters["dischtime"], errors="coerce")
    icu["intime"] = pd.to_datetime(icu["intime"], errors="coerce")
    icu["outtime"] = pd.to_datetime(icu["outtime"], errors="coerce")

    tables["patients"] = patients
    tables["admissions"] = encounters
    tables["icustays"] = icu

    ctx.objects = {
        "counts": {
            "Patient": int(patients.subject_id.nunique()),
            "Encounter": int(len(encounters)),
            "ICUStay": int(len(icu)),
            "Diagnosis": int(len(tables["diagnoses_icd"])),
            "Procedure": int(len(tables["procedures_icd"])),
            "Prescription": int(len(tables["prescriptions"])),
            "Lab": int(len(tables["labevents"])),
        },
        "patients": patients,
        "encounters": encounters,
        "icu_stays": icu,
    }


def _relate_objects(ctx: GovernanceContext) -> None:
    objects = ctx.objects
    encounters = objects["encounters"]
    icu = objects["icu_stays"]
    admissions_ids = set(encounters["hadm_id"].dropna().astype("int64"))
    stay_ids = set(icu["stay_id"].dropna().astype("int64"))

    bad_admit = 0
    for _, row in encounters.iterrows():
        if (
            pd.notna(row["dischtime"])
            and pd.notna(row["admittime"])
            and row["dischtime"] < row["admittime"]
        ):
            bad_admit += 1
            ctx.add_issue(
                "TEMPORAL_INCONSISTENCY",
                "error",
                "admissions",
                f"出院时间早于入院时间（hadm_id={int(row.hadm_id)}）",
                subject=int(row.subject_id),
                record=f"admissions:{row.name}",
            )

    bad_icu = 0
    for _, row in icu.iterrows():
        if pd.notna(row["outtime"]) and pd.notna(row["intime"]) and row["outtime"] < row["intime"]:
            bad_icu += 1
            ctx.add_issue(
                "TEMPORAL_INCONSISTENCY",
                "error",
                "icustays",
                f"ICU 出科时间早于入科时间（stay_id={int(row.stay_id)}）",
                subject=int(row.subject_id),
                record=f"icustays:{row.name}",
            )
        if int(row.hadm_id) not in admissions_ids:
            ctx.add_issue(
                "OBJECT_ASSOCIATION",
                "error",
                "icustays",
                f"stay_id={int(row.stay_id)} 的 hadm_id 不在住院对象中",
                subject=int(row.subject_id),
                record=f"icustays:{row.name}",
            )

    objects["admission_ids"] = admissions_ids
    objects["stay_ids"] = stay_ids
    objects["windows"] = {
        int(row.hadm_id): (row["admittime"], row["dischtime"])
        for _, row in encounters.iterrows()
    }
    ctx.stats.update(
        {
            "bad_admission_windows": bad_admit,
            "bad_icu_windows": bad_icu,
            "encounters_linked_to_patient": int(
                encounters["subject_id"].isin(set(objects["patients"]["subject_id"])).sum()
            ),
        }
    )


def _event(
    ctx: GovernanceContext,
    subject_id: int,
    when,
    code: str,
    numeric: float | None,
    source: str,
    record_id: str,
    hadm: int | None = None,
    stay: int | None = None,
    text: str | None = None,
    extractor: str = "structured",
    assertion: str = "present",
) -> None:
    stamp = pd.to_datetime(when, errors="coerce") if when is not None else None
    if stamp is not None and pd.isna(stamp):
        stamp = None
    ctx.events.append(
        {
            "subject_id": int(subject_id),
            "time": stamp,
            "code": code,
            "numeric_value": numeric,
            "text_value": text,
            "hadm_id": hadm,
            "stay_id": stay,
            "source_table": source,
            "source_record_id": record_id,
            "mapping_version": MAPPING_VERSION,
            "extractor": extractor,
            "assertion": assertion,
        }
    )


def _map_structured(ctx: GovernanceContext) -> None:
    tables = ctx.tables
    objects = ctx.objects
    admission_ids = objects["admission_ids"]

    for _, row in objects["patients"].iterrows():
        _event(ctx, row.subject_id, None, f"GENDER//{row.gender}", None, "patients", f"patients:{row.name}")
        if pd.notna(row.get("anchor_age")):
            _event(
                ctx,
                row.subject_id,
                None,
                "DEMOGRAPHIC//ANCHOR_AGE",
                float(row.anchor_age),
                "patients",
                f"patients:{row.name}",
            )

    for _, row in objects["encounters"].iterrows():
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        _event(ctx, row.subject_id, row["admittime"], "ADMISSION//HOSPITAL", None,
               "admissions", f"admissions:{row.name}", hadm=hadm)
        if pd.notna(row["dischtime"]):
            _event(ctx, row.subject_id, row["dischtime"], "DISCHARGE//HOSPITAL", None,
                   "admissions", f"admissions:{row.name}", hadm=hadm)

    for _, row in objects["icu_stays"].iterrows():
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        stay = int(row.stay_id) if pd.notna(row.stay_id) else None
        unit = re.sub(r"[^A-Za-z0-9]+", "_", str(row.first_careunit)).strip("_") or "ICU"
        _event(ctx, row.subject_id, row["intime"], f"ICU_STAY//{unit}", None,
               "icustays", f"icustays:{row.name}", hadm=hadm, stay=stay)
        if pd.notna(row["outtime"]):
            _event(ctx, row.subject_id, row["outtime"], "ICU_DISCHARGE//HOSPITAL", None,
                   "icustays", f"icustays:{row.name}", hadm=hadm, stay=stay)

    dx_labels = {
        (int(row.icd_version), str(row.icd_code)): row.long_title
        for _, row in tables["d_icd_diagnoses"].iterrows()
        if pd.notna(row.icd_code)
    }
    dx_total = dx_matched = 0
    for _, row in tables["diagnoses_icd"].iterrows():
        if pd.isna(row.icd_code):
            ctx.add_issue("CODE_MAPPING", "warning", "diagnoses_icd", "缺少 icd_code，未输出",
                          subject=int(row.subject_id), record=f"diagnoses_icd:{row.name}")
            continue
        dx_total += 1
        key = (int(row.icd_version), str(row.icd_code))
        if key not in dx_labels:
            ctx.add_issue("CODE_NOT_IN_DICTIONARY", "warning", "diagnoses_icd",
                          f"ICD{key[0]} 编码 {key[1]} 不在 d_icd_diagnoses 中",
                          subject=int(row.subject_id), record=f"diagnoses_icd:{row.name}")
        else:
            dx_matched += 1
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        if hadm is not None and hadm not in admission_ids:
            ctx.add_issue("OBJECT_ASSOCIATION", "error", "diagnoses_icd",
                          f"诊断的 hadm_id={hadm} 不在住院对象中",
                          subject=int(row.subject_id), record=f"diagnoses_icd:{row.name}")
        _event(ctx, row.subject_id, None, f"DIAGNOSIS//ICD{int(row.icd_version)}//{row.icd_code}",
               None, "diagnoses_icd", f"diagnoses_icd:{row.name}", hadm=hadm)

    lab_items = set(pd.to_numeric(tables["d_labitems"]["itemid"], errors="coerce").dropna().astype("int64"))
    lab_total = lab_matched = 0
    for _, row in tables["labevents"].iterrows():
        if pd.isna(row.itemid) or pd.isna(row.charttime):
            ctx.add_issue("REQUIRED_FIELD", "warning", "labevents", "缺少 itemid 或 charttime，未输出",
                          subject=int(row.subject_id), record=f"labevents:{row.name}")
            continue
        lab_total += 1
        itemid = int(row.itemid)
        if itemid in lab_items:
            lab_matched += 1
        else:
            ctx.add_issue("CODE_NOT_IN_DICTIONARY", "warning", "labevents",
                          f"itemid={itemid} 不在 d_labitems 中",
                          subject=int(row.subject_id), record=f"labevents:{row.name}")
        unit = "UNKNOWN" if pd.isna(row.valueuom) else (
            re.sub(r"[^A-Za-z0-9%]+", "_", str(row.valueuom).strip()) or "UNKNOWN"
        )
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        if hadm is not None and hadm not in admission_ids:
            ctx.add_issue("OBJECT_ASSOCIATION", "error", "labevents",
                          f"检验的 hadm_id={hadm} 不在住院对象中",
                          subject=int(row.subject_id), record=f"labevents:{row.name}")
        _event(ctx, row.subject_id, row.charttime, f"LAB//MIMIC_ITEM//{itemid}//{unit}",
               None if pd.isna(row.valuenum) else float(row.valuenum),
               "labevents", f"labevents:{row.name}", hadm=hadm)

    med_total = 0
    for _, row in tables["prescriptions"].iterrows():
        if pd.isna(row.formulary_drug_cd) or not str(row.formulary_drug_cd).strip():
            ctx.add_issue("CODE_MAPPING", "warning", "prescriptions",
                          "缺少 formulary_drug_cd，未输出",
                          subject=int(row.subject_id), record=f"prescriptions:{row.name}")
            continue
        med_total += 1
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        if hadm is not None and hadm not in admission_ids:
            ctx.add_issue("OBJECT_ASSOCIATION", "error", "prescriptions",
                          f"处方的 hadm_id={hadm} 不在住院对象中",
                          subject=int(row.subject_id), record=f"prescriptions:{row.name}")
        drug_code = re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(row.formulary_drug_cd).strip())
        _event(ctx, row.subject_id, pd.to_datetime(row.starttime, errors="coerce"),
               f"MEDICATION//MIMIC_FORMULARY//{drug_code}", None, "prescriptions",
               f"prescriptions:{row.name}", hadm=hadm)

    ctx.stats.update(
        {
            "diagnosis_rows": dx_total,
            "diagnosis_codes_in_dictionary": dx_matched,
            "lab_rows": lab_total,
            "lab_items_in_dictionary": lab_matched,
            "prescription_rows": med_total,
            "structured_events": len(ctx.events),
        }
    )


def _extract_text(ctx: GovernanceContext) -> None:
    started = time.time()
    settings = ctx.settings
    notes, scan = scan_for_subjects(
        resolve_data_file(settings.note_root, "note", "discharge"),
        ["note_id", "subject_id", "hadm_id", "note_type", "charttime", "storetime", "text"],
        set(ctx.cohort),
        chunksize=10_000,
    )
    if not notes.empty:
        notes["charttime"] = pd.to_datetime(notes["charttime"], errors="coerce")
        notes["storetime"] = pd.to_datetime(notes["storetime"], errors="coerce")
        notes["subject_id"] = pd.to_numeric(notes["subject_id"], errors="coerce").astype("int64")
        notes = (
            notes.sort_values(["subject_id", "charttime"], kind="stable")
            .groupby("subject_id", sort=False)
            .head(settings.max_notes_per_subject)
        )

    objects = ctx.objects
    note_events = 0
    note_records: list[dict] = []
    for _, row in notes.iterrows():
        when = row["charttime"] if pd.notna(row["charttime"]) else row["storetime"]
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        if hadm is not None and hadm not in objects["admission_ids"]:
            ctx.add_issue("OBJECT_ASSOCIATION", "error", "discharge",
                          f"出院小结的 hadm_id={hadm} 不在住院对象中",
                          subject=int(row.subject_id), record=f"discharge:{row.note_id}")
        text = "" if pd.isna(row["text"]) else str(row["text"])
        common = dict(
            subject_id=int(row.subject_id),
            time=when,
            hadm_id=hadm,
            stay_id=None,
            source_table="discharge",
            source_record_id=f"discharge:{row.note_id}",
            mapping_version=MAPPING_VERSION,
            extractor="note_rules:v1",
        )
        ctx.events.append(
            {**common, "code": "NOTE//DISCHARGE", "numeric_value": None,
             "text_value": text[:20_000], "assertion": "present"}
        )
        note_events += 1
        diagnosis_lines: list[str] = []
        medication_names: list[str] = []
        for item in extract_note_events(text):
            if item["code"] == "NOTE_DIAGNOSIS//DISCHARGE":
                diagnosis_lines.append(item["text_value"])
            elif item["code"].startswith("NOTE_MEDICATION//"):
                medication_names.append(item.get("name") or item["text_value"])
            ctx.events.append(
                {
                    **common,
                    "code": item["code"],
                    "numeric_value": None,
                    "text_value": item["text_value"],
                    "assertion": item.get("assertion", "present"),
                }
            )
            note_events += 1
        note_records.append(
            {
                "subject_id": int(row.subject_id),
                "note_id": str(row.note_id),
                "diagnoses": diagnosis_lines,
                "medications": medication_names,
            }
        )
        sections = split_sections(text)
        for header in REQUIRED_NOTE_SECTIONS:
            if not sections.get(header):
                ctx.add_issue(
                    "TEXT_SECTION_MISSING", "warning", "discharge",
                    f"出院小结缺少必需章节: {header}",
                    subject=int(row.subject_id), record=f"discharge:{row.note_id}",
                )
        if when is not None and hadm in objects["windows"]:
            admit, disch = objects["windows"][hadm]
            if pd.notna(disch) and when > disch:
                ctx.add_issue("TEMPORAL_INCONSISTENCY", "warning", "discharge",
                              "出院小结时间晚于出院时间",
                              subject=int(row.subject_id), record=f"discharge:{row.note_id}")

    _check_text_consistency(ctx, note_records)
    ctx.stats.update(
        {
            "notes_used": int(len(notes)),
            "note_events": note_events,
            "notes_text_chars": int(notes["text"].fillna("").astype(str).str.len().sum()) if len(notes) else 0,
            "note_scan": scan,
            "text_seconds": round(time.time() - started, 1),
        }
    )


def run(ctx: GovernanceContext) -> None:
    _index_objects(ctx)
    _relate_objects(ctx)
    ctx.log(
        "对象索引与关联",
        f"构建 {ctx.objects['counts']['Patient']} 个 Patient、"
        f"{ctx.objects['counts']['Encounter']} 个 Encounter、"
        f"{ctx.objects['counts']['ICUStay']} 个 ICUStay；"
        f"关联异常 {ctx.stats['bad_admission_windows'] + ctx.stats['bad_icu_windows']} 处。",
    )

    _map_structured(ctx)
    ctx.log(
        "结构化事件映射",
        f"诊断编码命中字典 {ctx.stats['diagnosis_codes_in_dictionary']}/{ctx.stats['diagnosis_rows']}，"
        f"检验项目命中 {ctx.stats['lab_items_in_dictionary']}/{ctx.stats['lab_rows']}，"
        f"处方 {ctx.stats['prescription_rows']} 行。",
    )

    _extract_text(ctx)
    ctx.log(
        "文本事件抽取",
        f"读取出院小结 {ctx.stats['notes_used']} 份（每名患者最多 "
        f"{ctx.settings.max_notes_per_subject} 份），生成 {ctx.stats['note_events']} 条 NOTE_* 事件；"
        f"使用确定性规则，未调用大模型。",
    )
    ctx.log("组织与标准化", f"累计生成 {len(ctx.events)} 条候选事件。")


#: 出院小结必须具备的章节（缺失记 TEXT_SECTION_MISSING）
REQUIRED_NOTE_SECTIONS = (
    "HISTORY OF PRESENT ILLNESS",
    "DISCHARGE DIAGNOSIS",
    "DISCHARGE MEDICATIONS",
)

_STOPWORDS = {
    "with", "without", "from", "this", "that", "have", "been", "were", "which",
    "patient", "years", "year", "history", "left", "right", "other", "status",
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).strip()


def _tokens(text: str) -> set[str]:
    return {
        word for word in _normalize(text).split()
        if len(word) >= 4 and word not in _STOPWORDS
    }


def _check_text_consistency(ctx: GovernanceContext, records: list[dict]) -> None:
    """文本与结构化编码一致性：只标记、不改写。

    用药：出院用药名是否出现在该患者的结构化处方里；
    诊断：出院诊断文本与结构化 ICD 描述是否有词重合。
    """
    if not records:
        return
    tables = ctx.tables

    structured_drugs: dict[int, set[str]] = {}
    prescriptions = tables.get("prescriptions")
    if prescriptions is not None and "drug" in prescriptions.columns:
        for subject, group in prescriptions.groupby("subject_id"):
            structured_drugs[int(subject)] = {
                _normalize(name) for name in group["drug"].dropna().astype(str)
            }

    structured_diagnoses: dict[int, set[str]] = {}
    diagnoses = tables.get("diagnoses_icd")
    dictionary = tables.get("d_icd_diagnoses")
    if diagnoses is not None and dictionary is not None:
        merged = diagnoses.merge(
            dictionary, on=["icd_code", "icd_version"], how="left", suffixes=("", "_dict")
        )
        for subject, group in merged.groupby("subject_id"):
            tokens: set[str] = set()
            for title in group["long_title"].dropna().astype(str):
                tokens |= _tokens(title)
            structured_diagnoses[int(subject)] = tokens

    for record in records:
        subject = record["subject_id"]
        record_id = f"discharge:{record['note_id']}"

        known_drugs = structured_drugs.get(subject, set())
        if known_drugs:
            for name in record["medications"]:
                normalized = _normalize(name)
                if not normalized:
                    continue
                if not any(normalized in known or known in normalized for known in known_drugs):
                    ctx.add_issue(
                        "TEXT_DRUG_MISMATCH", "warning", "discharge",
                        f"出院用药未见于结构化处方: {name}",
                        subject=subject, record=record_id,
                    )

        note_tokens: set[str] = set()
        for line in record["diagnoses"]:
            note_tokens |= _tokens(line)
        coded_tokens = structured_diagnoses.get(subject, set())
        if note_tokens and coded_tokens and not (note_tokens & coded_tokens):
            ctx.add_issue(
                "TEXT_CODE_MISMATCH", "warning", "discharge",
                "出院诊断文本与结构化 ICD 描述无词重合",
                subject=subject, record=record_id,
            )
