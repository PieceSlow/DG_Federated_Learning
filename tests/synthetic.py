"""合成 MIMIC 小样本：用于在没有真实数据的环境里验证整条治理链路。

不包含任何真实患者信息，表结构按 MIMIC-IV 3.1 / MIMIC-IV-Note 2.2 的字段命名，
故意埋入几类质量问题，让质量校验阶段能被真正触发：

* 一条处方缺 ``formulary_drug_cd`` → ``CODE_MAPPING``；
* 一条检验的 ``itemid`` 不在字典中 → ``CODE_NOT_IN_DICTIONARY``；
* 一条检验缺 ``charttime`` → ``REQUIRED_FIELD``。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_synthetic_mimic(root: Path, cohort_size: int = 3) -> Path:
    """在 ``root`` 下写出 hosp/、icu/、note/ 三组 csv.gz，返回 MIMIC 结构化数据根目录。"""
    hosp = root / "mimic-iv-3.1" / "hosp"
    icu = root / "mimic-iv-3.1" / "icu"
    note = root / "note"
    for path in (hosp, icu, note):
        path.mkdir(parents=True, exist_ok=True)

    patients = pd.DataFrame(
        [
            [1, "F", 65, 2150, 2200, "2100-01-01"],
            [2, "M", 72, 2150, 2200, "2100-02-01"],
            [3, "F", 50, 2150, 2200, "2100-03-01"],
        ],
        columns=["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group", "dod"],
    )

    admissions = pd.DataFrame(
        [
            [11, 1, "2100-01-01 08:00:00", "2100-01-05 12:00:00", "EMERGENCY", "EW EMER.", 0],
            [12, 2, "2100-02-01 09:00:00", "2100-02-10 09:00:00", "URGENT", "DIRECT OBSERVATION", 1],
            [13, 3, "2100-03-01 10:00:00", "2100-03-03 10:00:00", "ELECTIVE", "SURGICAL SAME DAY ADMISSION", 0],
        ],
        columns=["hadm_id", "subject_id", "admittime", "dischtime", "admission_type",
                 "admission_location", "hospital_expire_flag"],
    )

    icustays = pd.DataFrame(
        [
            [21, 1, 11, "MICU", "MICU", "2100-01-01 09:00:00", "2100-01-03 09:00:00", 2.0],
            [22, 2, 12, "SICU", "SICU", "2100-02-02 09:00:00", "2100-02-08 09:00:00", 6.0],
            [23, 3, 13, "MICU", "MICU", "2100-03-01 11:00:00", "2100-03-02 11:00:00", 1.0],
        ],
        columns=["stay_id", "subject_id", "hadm_id", "first_careunit", "last_careunit",
                 "intime", "outtime", "los"],
    )

    diagnoses = pd.DataFrame(
        [
            [1, 11, 1, "I10", 10],
            [1, 11, 2, "E119", 10],
            [2, 12, 1, "C509", 10],
            [3, 13, 1, "4019", 9],
        ],
        columns=["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version"],
    )

    procedures = pd.DataFrame(
        [
            [1, 11, 1, "0BH17EZ", 10, "2100-01-02"],
            [2, 12, 1, "3893", 9, "2100-02-03"],
        ],
        columns=["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version", "chartdate"],
    )

    prescriptions = pd.DataFrame(
        [
            [2, 12, 501, "2100-02-02 10:00:00", "Aspirin", "MAIN", "81", "mg", "PO", "ASPIRIN"],
            [3, 13, 502, "2100-03-01 12:00:00", "Metoprolol", "MAIN", "25", "mg", "PO", "METOPROLOL"],
            [1, 11, 503, "2100-01-04 08:00:00", "Heparin", "MAIN", "5000", "units", "SC", None],
        ],
        columns=["subject_id", "hadm_id", "pharmacy_id", "starttime", "drug", "drug_type",
                 "dose_val_rx", "dose_unit_rx", "route", "formulary_drug_cd"],
    )

    labevents = pd.DataFrame(
        [
            [1, 1, 11, 50912, "2100-01-02 06:00:00", 1.2, "mg/dL", "normal"],
            [2, 2, 12, 50912, "2100-02-03 06:00:00", 2.4, "mg/dL", "abnormal"],
            [3, 1, 11, 99999, "2100-01-02 07:00:00", 5.0, "mg/dL", "normal"],
            [4, 1, 11, 50882, None, 24.0, "mEq/L", "normal"],
        ],
        columns=["labevent_id", "subject_id", "hadm_id", "itemid", "charttime", "valuenum", "valueuom", "flag"],
    )

    d_labitems = pd.DataFrame(
        [[50912, "Creatinine"], [50882, "Bicarbonate"]], columns=["itemid", "label"]
    )

    d_icd_diagnoses = pd.DataFrame(
        [
            ["I10", 10, "Essential (primary) hypertension"],
            ["E119", 10, "Type 2 diabetes mellitus without complications"],
            ["C509", 10, "Malignant neoplasm of breast, unspecified"],
            ["4019", 9, "Essential hypertension, unspecified"],
        ],
        columns=["icd_code", "icd_version", "long_title"],
    )

    d_icd_procedures = pd.DataFrame(
        [
            ["0BH17EZ", 10, "Insertion of endotracheal airway"],
            ["3893", 9, "Venous catheterization, not elsewhere classified"],
        ],
        columns=["icd_code", "icd_version", "long_title"],
    )

    notes = pd.DataFrame(
        [
            [
                901, 1, 11, "DS",
                "2100-01-05 10:00:00", "2100-01-05 11:00:00",
                "Chief Complaint: Chest pain.\n"
                "History of Present Illness:\n"
                " 65-year-old woman with chest pain radiating to the left arm.\n"
                "Discharge Diagnosis:\n"
                "1. Acute myocardial infarction\n"
                "2. Hypertension\n"
                "Discharge Medications:\n"
                "1. Aspirin 81 mg PO daily\n"
                "2. Metoprolol 25 mg PO BID\n",
            ],
            [
                902, 2, 12, "DS",
                "2100-02-10 08:00:00", "2100-02-10 09:00:00",
                "Chief Complaint: Fever.\n"
                "Discharge Diagnosis:\n"
                "1. Sepsis\n"
                "Discharge Medications:\n"
                "1. Piperacillin 4 g IV q8h\n",
            ],
        ],
        columns=["note_id", "subject_id", "hadm_id", "note_type", "charttime", "storetime", "text"],
    )

    for frame, path in (
        (patients, hosp / "patients.csv.gz"),
        (admissions, hosp / "admissions.csv.gz"),
        (diagnoses, hosp / "diagnoses_icd.csv.gz"),
        (procedures, hosp / "procedures_icd.csv.gz"),
        (prescriptions, hosp / "prescriptions.csv.gz"),
        (labevents, hosp / "labevents.csv.gz"),
        (d_labitems, hosp / "d_labitems.csv.gz"),
        (d_icd_diagnoses, hosp / "d_icd_diagnoses.csv.gz"),
        (d_icd_procedures, hosp / "d_icd_procedures.csv.gz"),
        (icustays, icu / "icustays.csv.gz"),
        (notes, note / "discharge.csv.gz"),
    ):
        frame.to_csv(path, index=False, compression="gzip")

    return root / "mimic-iv-3.1"
