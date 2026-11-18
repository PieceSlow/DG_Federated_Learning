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

from governance.alignment.transform import TRANSFORMS, strict_real

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
        # 与 assemble 的 pd.to_numeric(..., errors="coerce") 口径一致：
        # "25-50" 这种区间剂量必须是 NULL，而不是 SQLite CAST 出来的 25
        expression = strict_real(expression)
    return expression, f"mapping:{table}.{field}"


#: 不在本 SQL 内产出、由后处理补齐的列：``stay_id`` 是 ICU 时间窗挂接，
#: ``split`` / ``icd_chapter`` 是事件后处理的派生列。它们既不等于"匹配失败"，
#: 也不等于"该事件没有这个字段"，必须在台账里单独成类。
LINK_COLUMNS: dict[str, str] = {"stay_id": "icu_window"}
POSTPROCESS_COLUMNS: dict[str, str] = {
    "split": "患者哈希划分（sha256）",
    "icd_chapter": "按 ICD 编码映射章节",
}


def _global_derived(
    target_field: str, event_type: str, spec: dict[str, Any]
) -> tuple[str, str] | None:
    """由事件规范统一派生、不参与字段匹配的字段。"""
    if target_field == "event_type":
        return f"'{event_type}'", "derived"
    if target_field == "source_table":
        return f"'{spec['table']}'", "derived"
    if target_field in POSTPROCESS_COLUMNS:
        return (
            f"NULL /* 后处理：{POSTPROCESS_COLUMNS[target_field]} */",
            "postprocess",
        )
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
        return global_derived

    if target_field in LINK_COLUMNS:
        # 与 assemble 的 link_stay() 等价的后处理：按 [intime, outtime] 挂 ICU 停留
        return f"NULL /* link：{LINK_COLUMNS[target_field]} */", f"link:{LINK_COLUMNS[target_field]}"

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
        "link_pending": sorted(
            {
                f"{event}.{field}"
                for event, origins in usage.items()
                for field, origin in origins.items()
                if origin.startswith("link:")
            }
        ),
        "postprocess": sorted(
            {
                f"{event}.{field}"
                for event, origins in usage.items()
                for field, origin in origins.items()
                if origin == "postprocess"
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


#: 不参与逐列对账的来源：它们本来就不由这段 SQL 产出
_NOT_PRODUCED = {"missing", "postprocess"}


def _compared_fields(event_type: str, usage: dict[str, Any]) -> list[str]:
    origins = (usage.get("by_event") or {}).get(event_type, {})
    return [
        field
        for field in EVENT_FIELDS
        if field != "event_type"  # 分组键，不作为计数列参与对账
        and not origins.get(field, "missing") in _NOT_PRODUCED
        and not str(origins.get(field, "")).startswith("link:")
    ]


def validate_sql(
    sql: str,
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame,
    expected_by_type: dict[str, int] | pd.DataFrame,
    expected_frame: pd.DataFrame | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在内存 SQLite 里重跑生成的 SQL 并与事件表对账。

    对账分两层：**逐类型行数**（结构是否一致）与**逐列非空数**（取值是否一致）。
    只有行数对账时，``stay_id`` 这种"行数一样、整列是 NULL"的差异是测不出来的。
    ``expected_by_type`` 既可以传计数字典（旧调用方），也可以传事件表本身。
    """
    started = time.perf_counter()
    if isinstance(expected_by_type, pd.DataFrame):
        expected_frame = expected_frame if expected_frame is not None else expected_by_type
        expected_by_type = {
            str(key): int(value)
            for key, value in expected_frame["event_type"].value_counts().items()
        }
    connection = sqlite3.connect(":memory:")
    try:
        for name, frame in tables.items():
            _load_table(connection, name, frame)
        _load_table(connection, "discharge", notes)
        connection.executescript(sql)
        frame = pd.read_sql_query(
            "SELECT event_type, COUNT(*) AS n FROM clinical_events GROUP BY event_type",
            connection,
        )
        actual = {str(row.event_type): int(row.n) for row in frame.itertuples()}
        actual_total = int(sum(actual.values()))
        expected_total = int(sum(expected_by_type.values()))
        report: dict[str, Any] = {
            "status": "ok" if actual == expected_by_type else "mismatch",
            "rows": actual_total,
            "expected_rows": expected_total,
            "by_type": actual,
            "expected_by_type": expected_by_type,
        }

        if expected_frame is not None and usage:
            compared: list[str] = []
            mismatches: list[dict[str, Any]] = []
            for event_type in usage.get("by_event", {}):
                fields = _compared_fields(event_type, usage)
                subset = expected_frame[expected_frame["event_type"] == event_type]
                if subset.empty or not fields:
                    continue
                select = ", ".join(f'COUNT("{field}") AS "{field}"' for field in fields)
                row = pd.read_sql_query(
                    f"SELECT event_type, {select} FROM clinical_events "
                    "WHERE event_type = ? GROUP BY event_type",
                    connection,
                    params=(event_type,),
                )
                if row.empty:
                    continue
                for field in fields:
                    compared.append(f"{event_type}.{field}")
                    sql_count = int(row[field].iat[0] or 0)
                    expected_count = int(subset[field].notna().sum()) if field in subset else 0
                    if sql_count != expected_count:
                        mismatches.append(
                            {
                                "column": f"{event_type}.{field}",
                                "sql": sql_count,
                                "expected": expected_count,
                            }
                        )
            report["columns_compared"] = len(compared)
            report["column_mismatches"] = mismatches[:50]
            if mismatches:
                report["status"] = "mismatch"
            report["not_produced_here"] = sorted(
                {
                    f"{event}.{field}"
                    for event, origins in usage.get("by_event", {}).items()
                    for field, origin in origins.items()
                    if origin in _NOT_PRODUCED or str(origin).startswith("link:")
                }
            )

        report["note"] = "行数 + 逐列非空数；本 SQL 不产出的列见 not_produced_here。"
        report["elapsed_s"] = round(time.perf_counter() - started, 2)
        return report
    except Exception as exc:  # noqa: BLE001 - 生成/执行失败要进报告
        return {
            "status": "error",
            "error": str(exc),
            "elapsed_s": round(time.perf_counter() - started, 2),
        }
    finally:
        connection.close()
