"""对象级标签引擎：事实 / 派生 / 画像三类标签。

- **事实标签**：直接来自对象属性（如年龄）；
- **派生标签**：由规则计算（如住院天数）；
- **画像标签**：业务组合（如"老年 + 长住院 → 随访优先"）。

标签直接打在对象上（``object_type + object_id``），与图谱节点 ID 一致，便于按标签检索对象。
规则用 pandas ``DataFrame.eval`` 执行；规则文件在仓库内、受版本控制，不接受外部输入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

DEFINITIONS_PATH = Path(__file__).resolve().parent / "definitions.yaml"

OBJECT_TYPES = {"Patient", "Encounter", "ICUStay"}
LABEL_TYPES = {"fact", "derived", "profile", "manual"}

OUTPUT_COLUMNS = [
    "object_type",
    "object_id",
    "key",
    "value",
    "label_type",
    "source",
    "version",
    "computed_at",
]


@dataclass(frozen=True)
class LabelDefinition:
    key: str
    object: str
    label_type: str
    description: str
    rule: str | None = None
    value: Any = True


def load_definitions(path: Path | str | None = None) -> tuple[str, list[LabelDefinition]]:
    source = Path(path) if path else DEFINITIONS_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "labels" not in data:
        raise ValueError(f"标签定义格式错误: {source}")

    definitions: list[LabelDefinition] = []
    seen: set[str] = set()
    for item in data["labels"]:
        key = str(item.get("key") or "")
        obj = str(item.get("object") or "")
        label_type = str(item.get("type") or "")
        if not key or key in seen:
            raise ValueError(f"标签 key 缺失或重复: {key}")
        if obj not in OBJECT_TYPES:
            raise ValueError(f"标签 {key} 的 object 非法: {obj}")
        if label_type not in LABEL_TYPES:
            raise ValueError(f"标签 {key} 的 type 非法: {label_type}")
        if not item.get("rule") and "value" not in item:
            raise ValueError(f"标签 {key} 必须有 rule 或 value")
        seen.add(key)
        definitions.append(
            LabelDefinition(
                key=key,
                object=obj,
                label_type=label_type,
                description=str(item.get("description") or ""),
                rule=item.get("rule"),
                value=item.get("value", True),
            )
        )
    return str(data.get("version", "1.0")), definitions


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _eval_rule(frame: pd.DataFrame, definition: LabelDefinition) -> pd.Series:
    if not definition.rule:
        return pd.Series(False, index=frame.index)
    try:
        result = frame.eval(definition.rule, engine="python")
    except Exception as exc:  # noqa: BLE001 - 规则错误要指出是哪个标签
        raise ValueError(f"标签 {definition.key} 规则执行失败: {exc}") from exc
    result = result.fillna(False)
    if result.dtype != bool:
        result = result.astype(bool)
    return result


def _patient_frame(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    patients = tables["patients"].copy()
    admissions = tables.get("admissions")
    if admissions is not None:
        counts = admissions.groupby("subject_id").size()
        patients["n_admissions"] = patients["subject_id"].map(counts).fillna(0).astype(int)
    else:
        patients["n_admissions"] = 0
    return patients


def _encounter_frame(
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame | None,
    patient_flags: pd.DataFrame,
) -> pd.DataFrame:
    encounters = tables["admissions"].copy()
    encounters["admittime"] = pd.to_datetime(encounters["admittime"], errors="coerce")
    encounters["dischtime"] = pd.to_datetime(encounters["dischtime"], errors="coerce")
    encounters["los_days"] = (
        encounters["dischtime"] - encounters["admittime"]
    ).dt.total_seconds() / 86400

    for table_name, column, feature in (
        ("prescriptions", "hadm_id", "n_prescriptions"),
        ("diagnoses_icd", "hadm_id", "n_diagnoses"),
        ("procedures_icd", "hadm_id", "n_procedures"),
    ):
        frame = tables.get(table_name)
        if frame is not None:
            counts = frame.groupby(column).size()
            encounters[feature] = encounters["hadm_id"].map(counts).fillna(0).astype(int)
        else:
            encounters[feature] = 0

    if notes is not None and not notes.empty:
        counts = notes.dropna(subset=["hadm_id"]).groupby("hadm_id").size()
        encounters["n_notes"] = encounters["hadm_id"].map(counts).fillna(0).astype(int)
    else:
        encounters["n_notes"] = 0

    if not patient_flags.empty:
        encounters = encounters.merge(patient_flags, on="subject_id", how="left")
    return encounters


def _icu_frame(tables: dict[str, pd.DataFrame], encounter_flags: pd.DataFrame) -> pd.DataFrame:
    icu = tables["icustays"].copy()
    icu["intime"] = pd.to_datetime(icu["intime"], errors="coerce")
    icu["outtime"] = pd.to_datetime(icu["outtime"], errors="coerce")
    icu["los_days"] = (icu["outtime"] - icu["intime"]).dt.total_seconds() / 86400
    if not encounter_flags.empty:
        icu = icu.merge(encounter_flags, on="hadm_id", how="left")
    return icu


def _collect(
    frame: pd.DataFrame,
    definitions: list[LabelDefinition],
    object_type: str,
    id_column: str,
    version: str,
    now: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for definition in definitions:
        if definition.object != object_type:
            continue
        mask = _eval_rule(frame, definition)
        frame[f"label_{definition.key}"] = mask
        for value in frame.loc[mask, id_column]:
            if pd.isna(value):
                continue
            rows.append(
                {
                    "object_type": object_type,
                    "object_id": f"{object_type}:{int(value)}",
                    "key": definition.key,
                    "value": _as_text(definition.value),
                    "label_type": definition.label_type,
                    "source": "rule",
                    "version": version,
                    "computed_at": now,
                }
            )
    return rows


def compute_labels(
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame | None = None,
    manual_path: Path | str | None = None,
    definitions: list[LabelDefinition] | None = None,
    version: str | None = None,
) -> pd.DataFrame:
    """计算对象标签；manual_path 提供的手动标签会覆盖同名自动标签。"""
    if definitions is None or version is None:
        loaded_version, loaded = load_definitions()
        definitions = definitions or loaded
        version = version or loaded_version
    now = datetime.now(timezone.utc).isoformat()

    patients = _patient_frame(tables)
    patient_rows = _collect(patients, definitions, "Patient", "subject_id", version, now)
    patient_flags = patients[
        ["subject_id"] + [f"label_{d.key}" for d in definitions if d.object == "Patient"]
    ].rename(columns={f"label_{d.key}": f"patient_{d.key}" for d in definitions if d.object == "Patient"})

    encounters = _encounter_frame(tables, notes, patient_flags)
    encounter_rows = _collect(encounters, definitions, "Encounter", "hadm_id", version, now)
    encounter_flags = encounters[
        ["hadm_id"] + [f"label_{d.key}" for d in definitions if d.object == "Encounter"]
    ].rename(columns={f"label_{d.key}": f"encounter_{d.key}" for d in definitions if d.object == "Encounter"})

    icu = _icu_frame(tables, encounter_flags)
    icu_rows = _collect(icu, definitions, "ICUStay", "stay_id", version, now)

    auto = pd.DataFrame(patient_rows + encounter_rows + icu_rows, columns=OUTPUT_COLUMNS)

    manual = _read_manual(manual_path, version, now)
    if manual is not None and not manual.empty:
        keys = set(zip(manual["object_type"], manual["object_id"], manual["key"]))
        if not auto.empty:
            mask = [
                (row.object_type, row.object_id, row.key) not in keys
                for row in auto.itertuples()
            ]
            auto = auto[mask]
        auto = pd.concat([auto, manual], ignore_index=True)

    if auto.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return auto.sort_values(["object_type", "object_id", "key"]).reset_index(drop=True)


def _read_manual(path: Path | str | None, version: str, now: str) -> pd.DataFrame | None:
    if path is None:
        return None
    manual_path = Path(path)
    if not manual_path.exists():
        raise FileNotFoundError(f"手动标签文件不存在: {manual_path}")
    frame = pd.read_csv(manual_path, dtype=str)
    required = {"object_type", "object_id", "key", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"手动标签缺少列: {', '.join(sorted(missing))}")
    frame = frame.copy()
    frame["label_type"] = "manual"
    frame["source"] = "manual"
    frame["version"] = version
    frame["computed_at"] = now
    return frame[OUTPUT_COLUMNS]


def definitions_catalog(definitions: list[LabelDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "key": definition.key,
            "object": definition.object,
            "type": definition.label_type,
            "rule": definition.rule,
            "value": _as_text(definition.value),
            "description": definition.description,
        }
        for definition in definitions
    ]
