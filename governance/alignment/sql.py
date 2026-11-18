"""从字段映射生成可执行 SQL，并用 SQLite 验证。

生成的是"组织汇聚"的 SQL：四类事件表 UNION ALL 成统一事件视图。

**字段表达式由 ``mapping/field_level.csv``（schema matching 结果）决定**：
目标字段 → 匹配到的 (源表, 源字段) → ``别名.列``。这样"先找字段映射、再生成 SQL"
才是一条链；此前 SQL 由硬编码的四段规范生成，对齐结果没有任何下游消费者。

单个目标字段的取值来源按优先级解析：

1. ``literal``：事件规范常量（文书的 ``code='DISCHARGE'``），不来自任何列；
2. ``transform``：一对多转换（剂量串 = 剂量 + 单位 + 途径），见 :mod:`governance.alignment.transform`；
3. ``mapping``：字段级匹配结果；按事件类型只取本表与本次 JOIN 用到的源表；
4. ``derived``：由事件规范直接生成（``record_id`` / ``source_table`` / ``source_record_id`` …）；
5. 其余 → ``NULL``，并在 ``mapping_usage`` 里记为 ``missing``。

``build_sql_with_usage()`` 额外返回一份"字段来源台账"，用来回答
"对齐结果到底被谁消费了"：映射驱动多少字段、常量/派生/缺失各多少。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pandas as pd

from governance.alignment.transform import TRANSFORMS

#: 每个事件类型的"装配规范"：主表、JOIN、派生表达式、规范常量、一对多转换。
#: 只放**结构**与**非映射**信息；具体取值来自字段映射。
SPECS: dict[str, dict[str, Any]] = {
    "Diagnosis": {
        "table": "diagnoses_icd",
        "alias": "t",
        "joins": [
            {
                "table": "d_icd_diagnoses",
                "alias": "d",
                "sql": (
                    "LEFT JOIN d_icd_diagnoses d "
                    "ON CAST(d.icd_code AS TEXT) = CAST(t.icd_code AS TEXT) "
                    "AND CAST(d.icd_version AS TEXT) = CAST(t.icd_version AS TEXT)"
                ),
            }
        ],
        "literal": {},
        "transform": {},
        "derived": {
            "record_id": (
                "'DX:' || t.subject_id || ':' || t.hadm_id || ':ICD' || t.icd_version "
                "|| ':' || t.seq_num"
            ),
            "code_system": "'ICD' || t.icd_version",
            "sensitivity": "'L2'",
            "source_record_id": "'diagnoses_icd:' || (t.rowid - 1)",
        },
    },
    "Procedure": {
        "table": "procedures_icd",
        "alias": "t",
        "joins": [
            {
                "table": "d_icd_procedures",
                "alias": "p",
                "sql": (
                    "LEFT JOIN d_icd_procedures p "
                    "ON CAST(p.icd_code AS TEXT) = CAST(t.icd_code AS TEXT) "
                    "AND CAST(p.icd_version AS TEXT) = CAST(t.icd_version AS TEXT)"
                ),
            }
        ],
        "literal": {},
        "transform": {},
        "derived": {
            "record_id": (
                "'PX:' || t.subject_id || ':' || t.hadm_id || ':ICD' || t.icd_version "
                "|| ':' || t.seq_num"
            ),
            "code_system": "'ICD' || t.icd_version",
            "sensitivity": "'L2'",
            "source_record_id": "'procedures_icd:' || (t.rowid - 1)",
        },
    },
    "Prescription": {
        "table": "prescriptions",
        "alias": "t",
        "joins": [],
        "literal": {},
        # 一对多：剂量 + 单位 + 途径 → text_value（笔记里"一对多需要一个转换 tool"）
        "transform": {"text_value": "dose_text"},
        "derived": {
            "record_id": (
                "'RX:' || t.subject_id || ':' || t.hadm_id || ':' "
                "|| COALESCE(t.pharmacy_id, 'NA') || ':' || t.rowid"
            ),
            "code_system": "'MIMIC_FORMULARY'",
            "sensitivity": "'L2'",
            "source_record_id": "'prescriptions:' || (t.rowid - 1)",
        },
    },
    "ClinicalNote": {
        "table": "discharge",
        "alias": "t",
        "joins": [],
        # 文书的事件编码由规范定义，不是某一列的取值
        "literal": {"code": "'DISCHARGE'", "display": "'Discharge summary'"},
        "transform": {},
        "derived": {
            "record_id": "'NOTE:' || t.note_id",
            "code_system": "'MIMIC_NOTE'",
            "sensitivity": "'L3'",
            "source_record_id": "'discharge:' || t.note_id",
        },
    },
}

#: clinical_events 的固定字段
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


def _mapping_index(field_map: pd.DataFrame | None) -> dict[str, list[tuple[str, str, float, str]]]:
    """目标字段 → [(源表, 源字段, 分数, 状态)]。"""
    index: dict[str, list[tuple[str, str, float, str]]] = {}
    if field_map is None or getattr(field_map, "empty", True):
        return index
    frame = field_map
    if "target_table" in frame.columns:
        frame = frame[frame["target_table"] == "clinical_events"]
    if frame.empty or "target_field" not in frame.columns:
        return index
    for row in frame.itertuples():
        index.setdefault(str(row.target_field), []).append(
            (
                str(row.source_table),
                str(row.source_field),
                float(getattr(row, "score", 0.0) or 0.0),
                str(getattr(row, "status", "") or ""),
            )
        )
    return index


def _aliases(spec: dict[str, Any]) -> dict[str, str]:
    aliases = {spec["table"]: spec["alias"]}
    for join in spec.get("joins", ()):
        aliases[join["table"]] = join["alias"]
    return aliases


def _resolve_from_mapping(
    target_field: str,
    spec: dict[str, Any],
    index: dict[str, list[tuple[str, str, float, str]]],
) -> tuple[str, str] | None:
    """从匹配结果里挑出该事件类型可用的源字段。"""
    aliases = _aliases(spec)
    candidates = [item for item in index.get(target_field, ()) if item[0] in aliases]
    if not candidates:
        return None
    # 优先本表（同一行数据的字段），其次 auto，最后按分数
    candidates.sort(key=lambda item: (item[0] != spec["table"], item[3] != "auto", -item[2]))
    table, field, _, _ = candidates[0]
    expression = f"{aliases[table]}.{field}"
    if target_field == "numeric_value":
        expression = f"CAST({expression} AS REAL)"
    return expression, f"mapping:{table}.{field}"


def _global_derived(target_field: str, event_type: str, spec: dict[str, Any]) -> str | None:
    """由事件规范统一派生、不参与字段匹配的字段。"""
    if target_field == "event_type":
        return f"'{event_type}'"
    if target_field == "source_table":
        return f"'{spec['table']}'"
    if target_field == "split":
        return "NULL /* 由患者哈希划分，后处理计算 */"
    if target_field == "icd_chapter":
        return "NULL /* 由 ICD 编码后处理映射 */"
    return None


def _expression(
    target_field: str,
    event_type: str,
    spec: dict[str, Any],
    index: dict[str, list[tuple[str, str, float, str]]],
) -> tuple[str, str]:
    """返回 (SQL 表达式, 来源标签)。来源标签形如 mapping:表.字段 / transform:x / spec / derived / missing。"""
    literal = spec.get("literal", {})
    if target_field in literal:
        return literal[target_field], "spec"

    transforms = spec.get("transform", {})
    if target_field in transforms:
        name = transforms[target_field]
        return TRANSFORMS[name]["build"](spec["alias"]), f"transform:{name}"

    resolved = _resolve_from_mapping(target_field, spec, index)
    if resolved is not None:
        return resolved

    derived = spec.get("derived", {}).get(target_field)
    if derived is not None:
        return derived, "derived"

    global_derived = _global_derived(target_field, event_type, spec)
    if global_derived is not None:
        return global_derived, "derived"

    return "NULL", "missing"


def _summarize(usage: dict[str, dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for origins in usage.values():
        for origin in origins.values():
            kind = origin.split(":", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def build_sql_with_usage(
    field_map: pd.DataFrame | None = None,
) -> tuple[str, dict[str, Any]]:
    """生成 SQL 并返回"字段来源台账"。

    ``field_map`` 为空时仍能产出可执行 SQL（除派生/常量外的字段为 NULL），
    便于在没有对齐结果时单独验证 SQL 结构。
    """
    index = _mapping_index(field_map)
    selects: list[str] = []
    usage: dict[str, dict[str, str]] = {}

    for event_type, spec in SPECS.items():
        origins: dict[str, str] = {}
        columns: list[str] = []
        for target in EVENT_FIELDS:
            expression, origin = _expression(target, event_type, spec, index)
            columns.append(f"    {expression} AS {target}")
            origins[target] = origin
        usage[event_type] = origins

        joins = "".join(f"\n{join['sql']}" for join in spec.get("joins", ()))
        select = (
            f"-- {event_type}\nSELECT\n"
            + ",\n".join(columns)
            + f"\nFROM {spec['table']} t{joins}"
        )
        selects.append(select)

    header = (
        "-- 由 governance.alignment.sql 生成；请勿手工编辑。\n"
        "-- 字段表达式来自 mapping/field_level.csv（schema matching 结果）；\n"
        "-- 派生字段与规范常量不参与匹配，见各事件段前的注释。\n"
        "-- 注意：split 需按患者哈希后处理；icd_chapter 需按编码映射后处理。\n"
    )
    sql = (
        header
        + "DROP VIEW IF EXISTS clinical_events;\nCREATE VIEW clinical_events AS\n"
        + "\nUNION ALL\n".join(selects)
        + ";\n"
    )
    report = {
        "summary": _summarize(usage),
        "by_event": usage,
        "mapping_rows": int(len(field_map)) if field_map is not None else 0,
        "mapped_targets": sorted(
            {
                origin.split(":", 1)[1]
                for origins in usage.values()
                for origin in origins.values()
                if origin.startswith("mapping:")
            }
        ),
        "missing": sorted(
            {
                f"{event}.{field}"
                for event, origins in usage.items()
                for field, origin in origins.items()
                if origin == "missing"
            }
        ),
    }
    return sql, report


def build_sql(field_map: pd.DataFrame | None = None) -> str:
    return build_sql_with_usage(field_map)[0]


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
