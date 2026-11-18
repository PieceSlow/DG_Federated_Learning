"""MEDS 规范适配层。

MEDS（Medical Event Data Standard）规定事件流的核心列与元数据文件布局。本模块集中
处理三件事，避免规范细节散落在流程各处：

* 加载官方 schema 并做校验；
* 定义编码格式（``命名空间//值``）与事件表 Arrow schema；
* 生成患者级划分（train / tuning / held_out，按 sha256 稳定划分）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from governance.config import meds_src, require_meds_src

#: 事件表映射版本；换映射规则时递增，便于区分历史产物
MAPPING_VERSION = "mimic-iv-3.1-to-meds-v4"

#: 编码格式：首段大写命名空间，之后至少一个 ``//值`` 段
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*(//[^/]+)+$")

DATASET_NAME = "MIMIC-IV v3.1 local ICU cohort (MEDS)"
DATASET_VERSION = "3.0"
ETL_NAME = "Medical data governance pipeline"
ETL_VERSION = "3.0.0"
MEDS_VERSION = "0.4"


def subject_split(subject: int) -> str:
    """按 sha256 把患者稳定划分到 train / tuning / held_out（70 / 15 / 15）。"""
    bucket = int(hashlib.sha256(f"split-v1:{subject}".encode()).hexdigest(), 16) % 100
    return "train" if bucket < 70 else ("tuning" if bucket < 85 else "held_out")


def load_schema():
    """加载官方 MEDS schema（vendored 副本，见 third_party/meds/NOTICE.md）。"""
    schema_path = require_meds_src(meds_src()) / "meds" / "schema.py"
    spec = importlib.util.spec_from_file_location("meds_schema_local", schema_path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件存在时不会发生
        raise RuntimeError(f"无法加载 MEDS schema: {schema_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event_store_schema() -> pa.Schema:
    """事件仓储 ``data/events.parquet`` 的完整 Schema（核心 5 列 + 6 个扩展列）。"""
    return pa.schema(
        [
            pa.field("subject_id", pa.int64(), nullable=False),
            pa.field("time", pa.timestamp("us"), nullable=True),
            pa.field("code", pa.string(), nullable=False),
            pa.field("numeric_value", pa.float32(), nullable=True),
            pa.field("text_value", pa.large_string(), nullable=True),
            pa.field("hadm_id", pa.int64(), nullable=True),
            pa.field("stay_id", pa.int64(), nullable=True),
            pa.field("source_table", pa.string(), nullable=True),
            pa.field("source_record_id", pa.string(), nullable=True),
            pa.field("mapping_version", pa.string(), nullable=True),
            pa.field("extractor", pa.string(), nullable=True),
            pa.field("assertion", pa.string(), nullable=True),
        ]
    )


def core_schema() -> pa.Schema:
    """MEDS 核心 5 列，用于在内存中做 schema 校验。"""
    return pa.schema(
        [
            pa.field("subject_id", pa.int64(), nullable=False),
            pa.field("time", pa.timestamp("us"), nullable=True),
            pa.field("code", pa.string(), nullable=False),
            pa.field("numeric_value", pa.float32(), nullable=True),
            pa.field("text_value", pa.large_string(), nullable=True),
        ]
    )


def events_to_frame(events: list[dict[str, Any]]) -> pd.DataFrame:
    """把事件字典列表整理成带正确 dtype 的 DataFrame（供落盘与校验共用）。"""
    frame = pd.DataFrame(events)
    frame["subject_id"] = frame["subject_id"].astype("int64")
    frame["hadm_id"] = pd.array(frame["hadm_id"], dtype="Int64")
    frame["stay_id"] = pd.array(frame["stay_id"], dtype="Int64")
    frame["numeric_value"] = pd.to_numeric(frame["numeric_value"], errors="coerce").astype("float32")
    frame["text_value"] = frame["text_value"].astype("object")
    if "assertion" not in frame.columns:
        frame["assertion"] = "present"
    frame["assertion"] = frame["assertion"].fillna("present").astype("object")
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    return frame.sort_values(
        ["subject_id", "time"], na_position="first", kind="stable"
    ).reset_index(drop=True)


def dataset_metadata() -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "etl_name": ETL_NAME,
        "etl_version": ETL_VERSION,
        "meds_version": MEDS_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "license": "PhysioNet Credentialed Health Data License v1.5.0",
        "description": "Local only. Derived row-level data must not be redistributed.",
        "raw_source_id_columns": ["hadm_id", "stay_id", "source_record_id"],
        "other_extension_columns": ["source_table", "mapping_version", "extractor", "assertion"],
    }


def code_description(code: str, tables: dict[str, pd.DataFrame]) -> tuple[str, list[str]]:
    """为编码生成可读描述与父编码，便于跨中心交流与检索。"""
    if code.startswith("LAB//MIMIC_ITEM//"):
        parts = code.split("//")
        itemid = int(parts[2])
        unit = parts[3] if len(parts) > 3 else ""
        row = tables["d_labitems"].loc[tables["d_labitems"]["itemid"] == itemid]
        label = str(row.iloc[0]["label"]) if not row.empty else "Unknown lab item"
        return f"{label} [{unit}]", ["MIMIC_ITEM"]
    if code.startswith("DIAGNOSIS//ICD"):
        parts = code.split("//")
        version = parts[1].replace("ICD", "")
        icd = parts[2]
        table = tables["d_icd_diagnoses"]
        row = table.loc[
            (table["icd_code"].astype(str) == icd) & (table["icd_version"].astype(str) == version)
        ]
        title = str(row.iloc[0]["long_title"]) if not row.empty else icd
        return title, [f"ICD{version}"]
    if code.startswith("MEDICATION//MIMIC_FORMULARY//"):
        return code.split("//")[-1], ["MIMIC_FORMULARY"]
    if code.startswith("NOTE_"):
        return code.split("//", 1)[1].replace("_", " ").title(), ["MIMIC_IV_NOTE"]
    if code.startswith("GENDER//"):
        return {"F": "Female", "M": "Male"}.get(code.split("//")[1], code), []
    mapping = {
        "ADMISSION//HOSPITAL": "Hospital admission",
        "DISCHARGE//HOSPITAL": "Hospital discharge",
        "ICU_DISCHARGE//HOSPITAL": "ICU discharge",
        "DEMOGRAPHIC//ANCHOR_AGE": "Anchor age",
        "NOTE//DISCHARGE": "Discharge summary note",
    }
    return mapping.get(code, code), []
