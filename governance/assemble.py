"""阶段 05 · 多源汇聚。

把不同粒度、不同时间字段的多张源表汇成两张"可直接用"的表：

* ``clinical_events``：一行一条诊疗事件（诊断 / 操作 / 处方 / 文书），字段统一、
  带编码体系、章节标签、敏感级别与患者级划分；
* ``patient_360``：一行一名患者，把人口学、住院/ICU/诊断/操作/处方/文书聚合成宽表。

对齐原则：**按语义对齐而不是按字段名硬套**；诊断没有时间就留空，不臆测；ICU 只挂接
落在 ``[intime, outtime]`` 内的事件；聚合行用 subject 级血缘，不伪造一对一来源。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from governance.context import GovernanceContext
from governance.label import icd_chapter, sensitivity_for
from governance.meds import subject_split


def _note_rows(ctx: GovernanceContext) -> pd.DataFrame:
    notes = [event for event in ctx.events if event["code"] == "NOTE//DISCHARGE"]
    frame = pd.DataFrame(
        [
            {
                "subject_id": event["subject_id"],
                "hadm_id": event["hadm_id"],
                "time": event["time"],
                "text_value": event["text_value"],
                "source_record_id": event["source_record_id"],
                "note_id": str(event["source_record_id"]).split(":", 1)[1],
            }
            for event in notes
        ],
        columns=["subject_id", "hadm_id", "time", "text_value", "source_record_id", "note_id"],
    )
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    return frame


def _build_events(ctx: GovernanceContext) -> pd.DataFrame:
    tables = ctx.tables
    admissions = tables["admissions"]
    icustays = tables["icustays"].copy()
    diagnoses = tables["diagnoses_icd"]
    procedures = tables["procedures_icd"].copy()
    prescriptions = tables["prescriptions"]
    d_dx = tables["d_icd_diagnoses"]
    d_px = tables["d_icd_procedures"]

    procedures["chartdate"] = pd.to_datetime(procedures["chartdate"], errors="coerce")
    note_rows = _note_rows(ctx)

    dx_title = {(int(r.icd_version), str(r.icd_code)): r.long_title for _, r in d_dx.iterrows()}
    px_title = {(int(r.icd_version), str(r.icd_code)): r.long_title for _, r in d_px.iterrows()}

    rows: list[dict[str, Any]] = []
    for _, r in diagnoses.iterrows():
        ver = int(r.icd_version)
        code = str(r.icd_code)
        rows.append(
            {
                "record_id": f"DX:{int(r.subject_id)}:{int(r.hadm_id)}:ICD{ver}:{int(r.seq_num)}",
                "subject_id": int(r.subject_id),
                "hadm_id": int(r.hadm_id),
                "stay_id": None,
                "event_type": "Diagnosis",
                "event_time": None,
                "code_system": f"ICD{ver}",
                "code": code,
                "display": dx_title.get((ver, code), code),
                "numeric_value": None,
                "text_value": None,
                "icd_chapter": icd_chapter(ver, code),
                "source_table": "diagnoses_icd",
                "source_record_id": f"diagnoses_icd:{r.name}",
            }
        )

    for _, r in procedures.iterrows():
        ver = int(r.icd_version)
        code = str(r.icd_code)
        rows.append(
            {
                "record_id": f"PX:{int(r.subject_id)}:{int(r.hadm_id)}:ICD{ver}:{int(r.seq_num)}",
                "subject_id": int(r.subject_id),
                "hadm_id": int(r.hadm_id),
                "stay_id": None,
                "event_type": "Procedure",
                "event_time": r.chartdate,
                "code_system": f"ICD{ver}",
                "code": code,
                "display": px_title.get((ver, code), code),
                "numeric_value": None,
                "text_value": None,
                "icd_chapter": icd_chapter(ver, code),
                "source_table": "procedures_icd",
                "source_record_id": f"procedures_icd:{r.name}",
            }
        )

    for _, r in prescriptions.iterrows():
        dose = pd.to_numeric(r.dose_val_rx, errors="coerce")
        text = " ".join(
            str(x).strip()
            for x in (r.dose_val_rx, r.dose_unit_rx, r.route)
            if pd.notna(x) and str(x).strip()
        )
        rows.append(
            {
                "record_id": f"RX:{int(r.subject_id)}:{int(r.hadm_id)}:"
                f"{int(r.pharmacy_id) if pd.notna(r.pharmacy_id) else 'NA'}:{r.name}",
                "subject_id": int(r.subject_id),
                "hadm_id": int(r.hadm_id),
                "stay_id": None,
                "event_type": "Prescription",
                "event_time": pd.to_datetime(r.starttime, errors="coerce"),
                "code_system": "MIMIC_FORMULARY",
                "code": str(r.formulary_drug_cd).strip() if pd.notna(r.formulary_drug_cd) else "",
                "display": str(r.drug).strip() if pd.notna(r.drug) else "",
                "numeric_value": float(dose) if pd.notna(dose) else None,
                "text_value": text,
                "icd_chapter": "N/A",
                "source_table": "prescriptions",
                "source_record_id": f"prescriptions:{r.name}",
            }
        )

    for _, r in note_rows.iterrows():
        text = "" if pd.isna(r.text_value) else str(r.text_value)
        rows.append(
            {
                "record_id": f"NOTE:{r.note_id}",
                "subject_id": int(r.subject_id),
                "hadm_id": int(r.hadm_id) if pd.notna(r.hadm_id) else None,
                "stay_id": None,
                "event_type": "ClinicalNote",
                "event_time": r["time"],
                "code_system": "MIMIC_NOTE",
                "code": "DISCHARGE",
                "display": "Discharge summary",
                "numeric_value": None,
                "text_value": text,
                "icd_chapter": "N/A",
                "source_table": "discharge",
                "source_record_id": r.source_record_id,
            }
        )

    events = pd.DataFrame(rows)

    icu_tmp = icustays.copy()
    icu_tmp["intime"] = pd.to_datetime(icu_tmp["intime"], errors="coerce")
    icu_tmp["outtime"] = pd.to_datetime(icu_tmp["outtime"], errors="coerce")
    stay_windows: dict[int, list[tuple[Any, Any, int]]] = {}
    for _, r in icu_tmp.iterrows():
        stay_windows.setdefault(int(r.hadm_id), []).append((r.intime, r.outtime, int(r.stay_id)))

    def link_stay(hadm: Any, when: Any) -> int | None:
        if pd.isna(hadm):
            return None
        stays = stay_windows.get(int(hadm), [])
        if not stays:
            return None
        if pd.isna(when):
            return stays[0][2] if len(stays) == 1 else None
        for start, end, stay in stays:
            if pd.notna(start) and pd.notna(end) and start <= when <= end:
                return stay
        return None

    events["stay_id"] = [link_stay(h, t) for h, t in zip(events["hadm_id"], events["event_time"])]
    events["split"] = events["subject_id"].map(lambda s: subject_split(int(s)))
    events["sensitivity"] = events["event_type"].map(sensitivity_for)
    events = events.sort_values(
        ["subject_id", "event_time"], na_position="first", kind="stable"
    ).reset_index(drop=True)
    events["text_value"] = events["text_value"].astype("object")
    return events


def _build_patient_360(ctx: GovernanceContext, events: pd.DataFrame) -> pd.DataFrame:
    patients = ctx.tables["patients"]
    admissions = ctx.tables["admissions"].copy()
    icustays = ctx.tables["icustays"]

    admissions["los_days"] = (
        pd.to_datetime(admissions["dischtime"], errors="coerce")
        - pd.to_datetime(admissions["admittime"], errors="coerce")
    ).dt.total_seconds() / 86400

    dx_counts = events[events.event_type == "Diagnosis"].groupby("subject_id").size()
    px_counts = events[events.event_type == "Procedure"].groupby("subject_id").size()
    rx_counts = events[events.event_type == "Prescription"].groupby("subject_id").size()
    note_agg = events[events.event_type == "ClinicalNote"].groupby("subject_id").agg(
        n_notes=("record_id", "size"),
        text_chars=("text_value", lambda s: int(sum(len(str(x)) for x in s))),
    )
    dx_top = (
        events[events.event_type == "Diagnosis"]
        .groupby(["subject_id", "display"]).size().reset_index(name="n")
        .sort_values(["subject_id", "n", "display"], ascending=[True, False, True])
        .drop_duplicates("subject_id").set_index("subject_id")["display"]
    )
    rx_top = (
        events[events.event_type == "Prescription"]
        .groupby(["subject_id", "display"]).size().reset_index(name="n")
        .sort_values(["subject_id", "n", "display"], ascending=[True, False, True])
        .drop_duplicates("subject_id").set_index("subject_id")["display"]
    )
    adm_agg = admissions.groupby("subject_id").agg(
        n_admissions=("hadm_id", "size"),
        total_los_days=("los_days", "sum"),
        mortality=("hospital_expire_flag", "max"),
        first_admit=("admittime", "min"),
        last_discharge=("dischtime", "max"),
    )
    icu_counts = icustays.groupby("subject_id").size().rename("n_icu")

    p360 = patients.set_index("subject_id")[["gender", "anchor_age", "anchor_year"]].copy()
    p360 = p360.join(adm_agg).join(icu_counts).join(dx_counts.rename("n_diagnosis"))
    p360 = p360.join(px_counts.rename("n_procedure")).join(rx_counts.rename("n_prescription")).join(note_agg)
    p360["top_diagnosis"] = dx_top
    p360["top_drug"] = rx_top
    for col in ("n_admissions", "n_icu", "n_diagnosis", "n_procedure", "n_prescription", "n_notes"):
        p360[col] = p360[col].fillna(0).astype("int64")
    p360["text_chars"] = p360["text_chars"].fillna(0).astype("int64")
    p360["total_los_days"] = p360["total_los_days"].fillna(0.0).round(2)
    p360["mortality"] = p360["mortality"].fillna(0).astype("int64")
    p360 = p360.reset_index()
    p360["split"] = p360["subject_id"].map(lambda s: subject_split(int(s)))
    p360["sensitivity"] = "L3"
    return p360.sort_values("subject_id").reset_index(drop=True)


def run(ctx: GovernanceContext) -> None:
    events = _build_events(ctx)
    p360 = _build_patient_360(ctx, events)

    ctx.clinical_events = events
    ctx.patient_360 = p360
    ctx.stats.update(
        {
            "clinical_events_rows": int(len(events)),
            "clinical_events_by_type": {k: int(v) for k, v in events["event_type"].value_counts().items()},
            "patient_360_rows": int(len(p360)),
            "stay_linked": int(events["stay_id"].notna().sum()),
            "note_text_chars": int(
                events.loc[events.event_type == "ClinicalNote", "text_value"].str.len().sum()
            ),
        }
    )

    dest = ctx.settings.governance / "dataset"
    dest.mkdir(parents=True, exist_ok=True)
    events_out = _typed_events(events)
    pq.write_table(pa.Table.from_pandas(events_out, preserve_index=False), dest / "clinical_events.parquet")

    p360_out = p360.copy()
    p360_out["first_admit"] = pd.to_datetime(p360_out["first_admit"], errors="coerce")
    p360_out["last_discharge"] = pd.to_datetime(p360_out["last_discharge"], errors="coerce")
    pq.write_table(pa.Table.from_pandas(p360_out, preserve_index=False), dest / "patient_360.parquet")

    ctx.log(
        "多源汇聚",
        f"统一事件表 {len(events)} 行（"
        + "、".join(f"{k} {v}" for k, v in events['event_type'].value_counts().items())
        + f"）；患者 360 宽表 {len(p360)} 行；"
        f"ICU 挂接 {ctx.stats['stay_linked']} 条（{ctx.stats['stay_linked'] / max(len(events), 1):.1%}）。",
    )


def _typed_events(events: pd.DataFrame) -> pd.DataFrame:
    """统一落盘 dtype，避免 parquet 里出现 object/混合类型。"""
    out = events[
        ["record_id", "subject_id", "hadm_id", "stay_id", "event_type", "event_time",
         "code_system", "code", "display", "numeric_value", "text_value", "icd_chapter",
         "source_table", "source_record_id", "split", "sensitivity"]
    ].copy()
    out["event_time"] = pd.to_datetime(out["event_time"], errors="coerce")
    out["numeric_value"] = pd.to_numeric(out["numeric_value"], errors="coerce").astype("float64")
    out["subject_id"] = out["subject_id"].astype("int64")
    out["hadm_id"] = pd.array(out["hadm_id"], dtype="Int64")
    out["stay_id"] = pd.array(out["stay_id"], dtype="Int64")
    out["text_value"] = out["text_value"].astype("object")
    return out.sort_values(
        ["subject_id", "event_time"], na_position="first", kind="stable"
    ).reset_index(drop=True)
