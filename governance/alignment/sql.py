"""从字段映射生成可执行 SQL，并用 SQLite 验证。

生成的是"组织汇聚"的 SQL：四类事件表 UNION ALL 成统一事件视图。
验证方式：把源表装进内存 SQLite，执行脚本，与 assemble 的事件表对账（行数/类型分布）。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pandas as pd

from governance.alignment.transform import TRANSFORMS

SPECS: dict[str, dict[str, Any]] = {
    "Diagnosis": {
        "table": "diagnoses_icd",
        "columns": ["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version"],
        "record_expr": "'DX:' || t.subject_id || ':' || t.hadm_id || ':ICD' || t.icd_version || ':' || t.seq_num",
        "event_time": "NULL",
        "code": "t.icd_code",
        "code_system": "'ICD' || t.icd_version",
        "display": "d.long_title",
        "numeric_value": "NULL",
        "text_value": "NULL",
        "sensitivity": "'L2'",
        "source_record": "'diagnoses_icd:' || (t.rowid - 1)",
        "join": (
            "LEFT JOIN d_icd_diagnoses d ON CAST(d.icd_code AS TEXT) = CAST(t.icd_code AS TEXT) "
            "AND CAST(d.icd_version AS TEXT) = CAST(t.icd_version AS TEXT)"
        ),
    },
    "Procedure": {
        "table": "procedures_icd",
        "columns": ["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version", "chartdate"],
        "record_expr": "'PX:' || t.subject_id || ':' || t.hadm_id || ':ICD' || t.icd_version || ':' || t.seq_num",
        "event_time": "t.chartdate",
        "code": "t.icd_code",
        "code_system": "'ICD' || t.icd_version",
        "display": "p.long_title",
        "numeric_value": "NULL",
        "text_value": "NULL",
        "sensitivity": "'L2'",
        "source_record": "'procedures_icd:' || (t.rowid - 1)",
        "join": (
            "LEFT JOIN d_icd_procedures p ON CAST(p.icd_code AS TEXT) = CAST(t.icd_code AS TEXT) "
            "AND CAST(p.icd_version AS TEXT) = CAST(t.icd_version AS TEXT)"
        ),
    },
    "Prescription": {
        "table": "prescriptions",
        "columns": ["subject_id", "hadm_id", "pharmacy_id", "starttime", "drug",
                    "formulary_drug_cd", "dose_val_rx", "dose_unit_rx", "route"],
        "record_expr": (
            "'RX:' || t.subject_id || ':' || t.hadm_id || ':' || COALESCE(t.pharmacy_id, 'NA') "
            "|| ':' || t.rowid"
        ),
        "event_time": "t.starttime",
        "code": "t.formulary_drug_cd",
        "code_system": "'MIMIC_FORMULARY'",
        "display": "t.drug",
        "numeric_value": "CAST(t.dose_val_rx AS REAL)",
        "text_value": TRANSFORMS["dose_text"]["build"]("t"),
        "sensitivity": "'L2'",
        "source_record": "'prescriptions:' || (t.rowid - 1)",
        "join": "",
    },
    "ClinicalNote": {
        "table": "discharge",
        "columns": ["note_id", "subject_id", "hadm_id", "charttime", "text"],
        "record_expr": "'NOTE:' || t.note_id",
        "event_time": "t.charttime",
        "code": "'DISCHARGE'",
        "code_system": "'MIMIC_NOTE'",
        "display": "'Discharge summary'",
        "numeric_value": "NULL",
        "text_value": "t.text",
        "sensitivity": "'L3'",
        "source_record": "'discharge:' || t.note_id",
        "join": "",
    },
}

#: 非简单列字段的表达式（其余字段若在源表中存在则直接引用）
_DERIVED = {
    "record_id",
    "event_type",
    "source_table",
    "source_record_id",
    "split",
    "sensitivity",
    "icd_chapter",
    "text_value",
    "numeric_value",
    "display",
    "code",
    "code_system",
    "event_time",
}


def _expression(target_field: str, event_type: str, spec: dict[str, Any]) -> str:
    if target_field == "record_id":
        return spec["record_expr"]
    if target_field == "event_type":
        return f"'{event_type}'"
    if target_field == "source_table":
        return f"'{spec['table']}'"
    if target_field == "source_record_id":
        return spec["source_record"]
    if target_field == "split":
        return "NULL /* 由患者哈希划分，后处理计算 */"
    if target_field == "sensitivity":
        return spec["sensitivity"]
    if target_field == "icd_chapter":
        return "NULL /* 由 ICD 编码后处理映射 */"
    if target_field in _DERIVED:
        return spec[target_field]
    if target_field in spec["columns"]:
        return f"t.{target_field}"
    return "NULL"


#: clinical_events 的固定字段；SQL 由事件映射规范生成，不依赖任何静态字段映射表。
EVENT_FIELDS = [
    "record_id",
    "subject_id",
    "hadm_id",
    "stay_id",
    "event_type",
    "event_time",
    "code_system",
    "code",
    "display",
    "numeric_value",
    "text_value",
    "icd_chapter",
    "source_table",
    "source_record_id",
    "split",
    "sensitivity",
]


def build_sql() -> str:
    selects: list[str] = []
    for event_type, spec in SPECS.items():
        columns = []
        for target in EVENT_FIELDS:
            expression = _expression(target, event_type, spec)
            columns.append(f"    {expression} AS {target}")
        select = (
            f"-- {event_type}\nSELECT\n" + ",\n".join(columns)
            + f"\nFROM {spec['table']} t\n{spec['join']}".rstrip()
        )
        selects.append(select)

    header = (
        "-- 由 governance.alignment.sql 生成；请勿手工编辑。\n"
        "-- 统一事件视图：诊断 / 操作 / 处方 / 文书四类 UNION ALL。\n"
        "-- 注意：split 需按患者哈希后处理；icd_chapter 需按编码映射后处理。\n"
    )
    return header + "DROP VIEW IF EXISTS clinical_events;\nCREATE VIEW clinical_events AS\n" + \
        "\nUNION ALL\n".join(selects) + ";\n"


def _load_table(connection: sqlite3.Connection, name: str, frame: pd.DataFrame) -> None:
    columns = [str(column) for column in frame.columns]
    if not columns:
        return
    ddl = f'CREATE TABLE "{name}" (' + ", ".join(f'"{column}" TEXT' for column in columns) + ")"
    connection.execute(ddl)
    placeholders = ",".join("?" for _ in columns)
    rows = [
        [None if pd.isna(value) else str(value) for value in row]
        for row in frame[columns].itertuples(index=False, name=None)
    ]
    connection.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})', rows)


def validate_sql(
    sql: str,
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame,
    expected_by_type: dict[str, int],
) -> dict[str, Any]:
    started = time.perf_counter()
    connection = sqlite3.connect(":memory:")
    try:
        for name, frame in tables.items():
            _load_table(connection, name, frame)
        _load_table(connection, "discharge", notes)
        connection.executescript(sql)
        frame = pd.read_sql_query("SELECT event_type, COUNT(*) AS n FROM clinical_events GROUP BY event_type", connection)
        actual = {str(row.event_type): int(row.n) for row in frame.itertuples()}
        actual_total = int(sum(actual.values()))
        expected_total = int(sum(expected_by_type.values()))
        status = "ok" if actual == expected_by_type else "mismatch"
        return {
            "status": status,
            "rows": actual_total,
            "expected_rows": expected_total,
            "by_type": actual,
            "expected_by_type": expected_by_type,
            "elapsed_s": round(time.perf_counter() - started, 2),
            "note": "行数与类型分布用于对账；文本/NULL 语义差异见 SQL 注释。",
        }
    except Exception as exc:  # noqa: BLE001 - 生成/执行失败要进报告
        return {
            "status": "error",
            "error": str(exc),
            "elapsed_s": round(time.perf_counter() - started, 2),
        }
    finally:
        connection.close()
