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

from governance.alignment.interface import check_events
from governance.alignment.targets import load_targets
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


#: 编码模板占位符的取值转换（``{列:转换}``）；不写转换按 ``raw`` 处理。
_VALUE_RENDERS = {
    "raw": lambda value: str(value).strip(),
    "int": lambda value: str(int(float(value))),
    "unit": lambda value: re.sub(r"[^A-Za-z0-9%]+", "_", str(value).strip()).strip("_") or "UNKNOWN",
    "drug": lambda value: re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(value).strip()),
}

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _blank(value) -> bool:
    """空值判定：None/NaN/空串都算缺；用于 ``emit_if`` 与派生列。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _int_or_none(value) -> int | None:
    if _blank(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _stamp(value):
    if _blank(value):
        return None
    stamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(stamp) else stamp


def _render_code(template: str, row: pd.Series, event_type: str) -> str:
    """按模板渲染编码；占位符必须是源行里真实存在的列，否则直接报错。"""

    def replace(match: re.Match) -> str:
        name, _, transform = match.group(1).partition(":")
        if name not in row.index:
            raise KeyError(f"{event_type} 的编码模板引用了不存在的列: {name}")
        render = _VALUE_RENDERS.get(transform or "raw")
        if render is None:
            raise KeyError(f"{event_type} 的编码模板使用了未声明的转换: {transform}")
        return render(row[name])

    return _PLACEHOLDER.sub(replace, template)


def _field_time(field, row: pd.Series):
    """时间列（可带 ``fallback`` 兜底列）。"""
    if not field.column:
        return None
    value = row.get(field.column)
    if _blank(value) and field.fallback:
        value = row.get(field.fallback)
    return _stamp(value)


def _field_numeric(field, row: pd.Series) -> float | None:
    if field.source != "derived" or not field.column:
        return None
    value = row.get(field.column)
    if _blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field_text(field, row: pd.Series) -> str | None:
    """文本列：``only_if_null`` 里的列有值时不再写文本（数值与文本二选一）。"""
    if field.source != "derived" or not field.column:
        return None
    for column in field.only_if_null:
        if not _blank(row.get(column)):
            return None
    value = row.get(field.column)
    if _blank(value):
        return None
    text = str(value)
    return text[: field.max_chars] if field.max_chars else text


def _attach_join(frame: pd.DataFrame, tables: dict[str, pd.DataFrame], target) -> pd.DataFrame:
    """按声明把关联表的列挂到源行上（保持左侧索引，``source_record_id`` 才稳定）。"""
    join = target.join
    if not join:
        return frame
    other = tables.get(str(join.get("table", "")))
    on = str(join.get("on_column", ""))  # 不能写 on:，YAML 1.1 会把 on 解析成布尔 True
    if other is None or other.empty or on not in frame.columns or on not in other.columns:
        return frame
    columns = [c for c in join.get("columns", []) if c in other.columns and c not in frame.columns]
    if not columns:
        return frame
    right = other[[on, *columns]].drop_duplicates(subset=[on]).set_index(on)
    return frame.join(right, on=on)


def _dictionary_keys(table: str, columns: list[str], tables: dict[str, pd.DataFrame]) -> set[tuple]:
    frame = tables.get(table)
    if frame is None or frame.empty:
        return set()
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        return set()
    return {
        tuple(str(value) for value in row)
        for row in frame[columns].itertuples(index=False, name=None)
    }


def _check_declared_columns(targets, tables: dict[str, pd.DataFrame]) -> None:
    """接口声明里引用的源列必须在已接入的表里存在。

    这条检查是"声明驱动实现"的护栏：过去 ``meds_targets.yaml`` 可以随手写一个不存在的
    列（例如 ``labevents.value``），实现里反正读不到、发出来是空值，而且没人发现。
    """
    missing: list[str] = []
    for event_type, target in targets.items():
        if target.extractor != "structured" or not target.table:
            continue
        frame = tables.get(target.table)
        if frame is None:
            continue
        join = target.join or {}
        join_columns = set(join.get("columns", ())) if join else set()
        for name, field in target.fields.items():
            columns: list[str] = []
            if field.column:
                columns.append(field.column)
            columns.extend(field.only_if_null)
            columns.extend(use.partition(".")[2] for use in field.uses)
            for column in columns:
                if not column:
                    continue
                if column in join_columns:
                    continue
                if column not in frame.columns:
                    missing.append(f"{event_type}.{name} → {target.table}.{column}")
    if missing:
        raise KeyError("接口声明引用了已接入表里不存在的列：" + "；".join(sorted(set(missing))))


def _emit_interface_events(ctx: GovernanceContext) -> None:
    """按 ``alignment/meds_targets.yaml`` 声明产出结构化事件。

    表名、编码模板、时间/数值/文本列都从**接口声明**读取，不再各自写死：
    过去这里是 6 段手写循环，而 ``meds_targets.yaml`` 只是旁边一张没人读的对照表，
    声明与实现因此长期不一致（例如声明 ``DIAGNOSIS.time required``，代码一直发 NULL）。
    """
    targets = load_targets()
    tables = ctx.tables
    _check_declared_columns(targets, tables)
    admission_ids = ctx.objects["admission_ids"]
    dictionary_cache: dict[tuple, set[tuple]] = {}
    counts: dict[str, dict[str, int]] = {}

    for event_type, target in targets.items():
        if target.extractor != "structured" or not target.table:
            continue  # NOTE 由 _extract_text 产出
        frame = tables.get(target.table)
        if frame is None or frame.empty:
            continue
        frame = _attach_join(frame, tables, target)

        dictionary_columns = list(target.dictionary.get("columns", ())) if target.dictionary else []
        dictionary_table = str(target.dictionary.get("table", "")) if target.dictionary else ""
        if dictionary_table:
            cache_key = (dictionary_table, tuple(dictionary_columns))
            if cache_key not in dictionary_cache:
                dictionary_cache[cache_key] = _dictionary_keys(dictionary_table, dictionary_columns, tables)
            known = dictionary_cache[cache_key]
        else:
            known = set()

        rows = emitted = hits = 0
        for name, row in frame.iterrows():
            record = f"{target.table}:{name}"
            subject = _int_or_none(row.get(target.keys.get("subject_id", "subject_id")))
            if subject is None:
                continue
            hadm = _int_or_none(row.get(target.keys["hadm_id"])) if "hadm_id" in target.keys else None
            stay = _int_or_none(row.get(target.keys["stay_id"])) if "stay_id" in target.keys else None

            if target.emit_if and any(_blank(row.get(column)) for column in target.emit_if):
                if target.skip_issue:
                    ctx.add_issue(
                        str(target.skip_issue.get("rule", "REQUIRED_FIELD")),
                        "warning",
                        target.table,
                        str(target.skip_issue.get("detail", "缺少必填源字段，未输出")),
                        subject=subject,
                        record=record,
                    )
                continue
            rows += 1

            if target.check_hadm and hadm is not None and hadm not in admission_ids:
                ctx.add_issue(
                    "OBJECT_ASSOCIATION", "error", target.table,
                    f"{target.description}的 hadm_id={hadm} 不在住院对象中",
                    subject=subject, record=record,
                )

            if dictionary_table:
                key = tuple(str(row.get(column)) for column in dictionary_columns)
                if key in known:
                    hits += 1
                else:
                    ctx.add_issue(
                        "CODE_NOT_IN_DICTIONARY", "warning", target.table,
                        f"{'/'.join(key)} 不在 {dictionary_table} 中",
                        subject=subject, record=record,
                    )

            _event(
                ctx,
                subject,
                _field_time(target.fields["time"], row),
                _render_code(target.fields["code"].template, row, event_type),
                _field_numeric(target.fields["numeric_value"], row),
                target.table,
                record,
                hadm=hadm,
                stay=stay,
                text=_field_text(target.fields["text_value"], row),
            )
            emitted += 1

        counts[event_type] = {"rows": rows, "emitted": emitted, "dictionary_hits": hits}

    ctx.stats.update(
        {
            "diagnosis_rows": counts.get("DIAGNOSIS", {}).get("rows", 0),
            "diagnosis_codes_in_dictionary": counts.get("DIAGNOSIS", {}).get("dictionary_hits", 0),
            "lab_rows": counts.get("LAB", {}).get("rows", 0),
            "lab_items_in_dictionary": counts.get("LAB", {}).get("dictionary_hits", 0),
            "prescription_rows": counts.get("MEDICATION", {}).get("emitted", 0),
            "structured_events": len(ctx.events),
            "interface_events_by_type": {key: value["emitted"] for key, value in counts.items()},
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
    note_spec = load_targets()["NOTE"]  # 文书事件的编码/时间/文本列同样来自接口声明
    note_events = 0
    note_records: list[dict] = []
    for _, row in notes.iterrows():
        when = _field_time(note_spec.fields["time"], row)
        hadm = int(row.hadm_id) if pd.notna(row.hadm_id) else None
        if hadm is not None and hadm not in objects["admission_ids"]:
            ctx.add_issue("OBJECT_ASSOCIATION", "error", "discharge",
                          f"出院小结的 hadm_id={hadm} 不在住院对象中",
                          subject=int(row.subject_id), record=f"discharge:{row.note_id}")
        text = "" if pd.isna(row["text"]) else str(row["text"])
        note_text = _field_text(note_spec.fields["text_value"], row) or ""
        common = dict(
            subject_id=int(row.subject_id),
            time=when,
            hadm_id=hadm,
            stay_id=None,
            source_table=note_spec.table,
            source_record_id=f"discharge:{row.note_id}",
            mapping_version=MAPPING_VERSION,
            extractor=note_spec.extractor,
        )
        ctx.events.append(
            {**common, "code": note_spec.fields["code"].template, "numeric_value": None,
             "text_value": note_text, "assertion": "present"}
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

    _emit_interface_events(ctx)
    ctx.log(
        "结构化事件映射",
        f"按接口声明产出 {len(ctx.events)} 条：诊断编码命中字典 "
        f"{ctx.stats['diagnosis_codes_in_dictionary']}/{ctx.stats['diagnosis_rows']}，"
        f"检验项目命中 {ctx.stats['lab_items_in_dictionary']}/{ctx.stats['lab_rows']}，"
        f"处方 {ctx.stats['prescription_rows']} 行。",
    )

    _extract_text(ctx)
    interface_check = check_events(ctx.events)
    ctx.stats["meds_interface_check"] = interface_check
    if interface_check["undeclared"]:
        ctx.add_issue(
            "INTERFACE_UNDECLARED_NAMESPACE",
            "warning",
            "events",
            "事件流里出现接口未声明的编码命名空间："
            + "、".join(interface_check["undeclared"]),
        )
    for item in interface_check["required_violations"]:
        ctx.add_issue(
            "INTERFACE_REQUIRED_MISSING",
            "warning",
            "events",
            f"{item['event_type']}.{item['column']} 声明为必填，但 {item['null']}/{item['rows']} 行为空",
        )
    ctx.log(
        "文本事件抽取",
        f"读取出院小结 {ctx.stats['notes_used']} 份（每名患者最多 "
        f"{ctx.settings.max_notes_per_subject} 份），生成 {ctx.stats['note_events']} 条 NOTE_* 事件；"
        f"使用确定性规则，未调用大模型。",
    )
    ctx.log(
        "组织与标准化",
        f"累计生成 {len(ctx.events)} 条候选事件；接口命名空间 "
        f"{len(interface_check['observed_namespaces'])} 个，未声明 "
        f"{len(interface_check['undeclared'])} 个，必填列空缺 {len(interface_check['required_violations'])} 处。",
    )


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
