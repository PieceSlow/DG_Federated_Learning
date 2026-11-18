"""对象级 KV 标签引擎：L1 资产级 / L2 记录级 / L3 概念级 / L4 断言级。

标签不是宽表里的列，而是打在对象上的断言：``object_type + object_id + tag_key``。
同一套 key 可以打在不同对象上（如 ``L1.sensitivity`` 同时用于 Event / Patient / Dataset）。

标签来源：
* ``field``：直接复制对象属性（如事件类型、编码体系）；
* ``rule``：pandas ``DataFrame.eval`` 计算的布尔断言（如多重用药）；
* ``constant``：定义即结论（如数据集共享边界）。

**标签建在图谱之上**：传入 ``graph``（``graph.build.GraphData``）时，
住院级的处方/诊断/操作条目数按边表计数、共病负担按"患者→住院→诊断"两跳的
章节去重计数、概念对象以图谱 Concept 节点为准；同时保留表/事件口径作为基线，
两者不一致时产出 ``LABEL_GRAPH_MISMATCH`` 告警，避免口径悄悄漂移。

规则文件在仓库内、受版本控制，不接受外部输入；UMLS 概念标签由
``concept_map`` 提供，缺失时 L3 语义类型降级为 ICD 章节。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from governance.concept_key import concept_object_id
from governance.icd import icd_chapter

DEFINITIONS_PATH = Path(__file__).resolve().parent / "definitions.yaml"

OBJECT_TYPES = {"Dataset", "Patient", "Encounter", "ICUStay", "Event", "Concept"}
LABEL_TYPES = {"fact", "derived", "profile", "manual", "field", "constant"}
LAYERS = {"L1", "L2", "L3", "L4"}

OUTPUT_COLUMNS = [
    "object_type",
    "object_id",
    "tag_key",
    "tag_value",
    "layer",
    "source",
    "confidence",
    "evidence",
    "version",
    "computed_at",
]


@dataclass(frozen=True)
class LabelDefinition:
    key: str
    object: str
    layer: str
    label_type: str
    description: str
    rule: str | None = None
    field: str | None = None
    value: Any = True


def _safe(name: str) -> str:
    """pandas eval 的列名不能带 "."，标签 key 里的点替换成下划线。"""
    return str(name).replace(".", "_")


def load_definitions(path: Path | str | None = None) -> tuple[str, list[LabelDefinition]]:
    source = Path(path) if path else DEFINITIONS_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "labels" not in data:
        raise ValueError(f"标签定义格式错误: {source}")

    definitions: list[LabelDefinition] = []
    seen: set[tuple[str, str]] = set()
    for item in data["labels"]:
        key = str(item.get("key") or "")
        obj = str(item.get("object") or "")
        layer = str(item.get("layer") or "")
        label_type = str(item.get("type") or "")
        if not key or (key, obj) in seen:
            raise ValueError(f"标签 key 缺失或重复: {key} / {obj}")
        if obj not in OBJECT_TYPES:
            raise ValueError(f"标签 {key} 的 object 非法: {obj}")
        if layer not in LAYERS:
            raise ValueError(f"标签 {key} 的 layer 非法: {layer}")
        if label_type not in LABEL_TYPES:
            raise ValueError(f"标签 {key} 的 type 非法: {label_type}")
        field = item.get("field")
        rule = item.get("rule")
        if label_type == "field" and not field:
            raise ValueError(f"标签 {key} 是 field 类型但缺少 field")
        if label_type in {"fact", "derived", "profile"} and not rule:
            raise ValueError(f"标签 {key} 是规则类型但缺少 rule")
        if label_type == "constant" and "value" not in item:
            raise ValueError(f"标签 {key} 是 constant 类型但缺少 value")
        seen.add((key, obj))
        definitions.append(
            LabelDefinition(
                key=key,
                object=obj,
                layer=layer,
                label_type=label_type,
                description=str(item.get("description") or ""),
                rule=rule,
                field=field,
                value=item.get("value", True),
            )
        )
    return str(data.get("version", "1.0")), definitions


def definitions_catalog(definitions: list[LabelDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "tag_key": definition.key,
            "layer": definition.layer,
            "object": definition.object,
            "type": definition.label_type,
            "rule": definition.rule,
            "field": definition.field,
            "value": _as_text(definition.value),
            "description": definition.description,
        }
        for definition in definitions
    ]


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        return f"{value:g}"
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


# ---------------------------------------------------------------- 图谱取数


def _graph_edges(graph: Any) -> dict[str, pd.DataFrame]:
    """取出图谱的边表字典；无图谱或结构不符时返回空字典。"""
    if graph is None:
        return {}
    edges = getattr(graph, "edges", None)
    return edges if isinstance(edges, dict) else {}


def _graph_nodes(graph: Any) -> pd.DataFrame | None:
    if graph is None:
        return None
    nodes = getattr(graph, "nodes", None)
    if not isinstance(nodes, pd.DataFrame) or nodes.empty:
        return None
    return nodes


def _edge_counts(graph: Any, relation: str) -> pd.Series | None:
    """按边的起点（``src_id``）计数；关系不存在时返回 None，调用方回退到表口径。

    关系**存在但为空**时返回空 Series —— 那是"图谱口径为 0"的有效信号，
    与"根本没有这张边表"必须区分开，否则对账永远发现不了图谱缺边。
    """
    frame = _graph_edges(graph).get(relation)
    if frame is None or "src_id" not in frame.columns:
        return None
    return frame.groupby("src_id").size()


def _chapters_by_subject(graph: Any) -> pd.Series | None:
    """两跳取共病章节：Patient ←has_encounter→ Encounter ←has_diagnosis→ Diagnosis。

    章节由 Diagnosis 节点的 ``icd_code``/``icd_version`` 现算，与
    ``clinical_events.icd_chapter`` 使用同一个 :func:`governance.icd.icd_chapter`。
    """
    nodes = _graph_nodes(graph)
    edges = _graph_edges(graph)
    if nodes is None:
        return None
    encounter_diagnosis = edges.get("has_diagnosis")
    patient_encounter = edges.get("has_encounter")
    if encounter_diagnosis is None or patient_encounter is None:
        return None

    diagnosis = nodes[nodes["object_type"] == "Diagnosis"]
    if diagnosis.empty:
        return None

    chapters: dict[str, str] = {}
    for row in diagnosis.itertuples():
        try:
            attributes = json.loads(row.attributes or "{}")
        except json.JSONDecodeError:
            continue
        code, version = attributes.get("icd_code"), attributes.get("icd_version")
        if not code or version in (None, ""):
            continue
        chapters[row.object_id] = icd_chapter(int(float(version)), code)
    if not chapters:
        return None

    subject_of_encounter = dict(
        zip(patient_encounter["dst_id"], patient_encounter["src_id"])
    )
    per_subject: dict[str, set[str]] = {}
    for row in encounter_diagnosis.itertuples():
        subject_id = subject_of_encounter.get(row.src_id)
        chapter = chapters.get(row.dst_id)
        if subject_id is None or chapter is None:
            continue
        per_subject.setdefault(subject_id, set()).add(chapter)
    # 边表为空也要返回空 Series：那是"图谱口径=0"的有效信号，不能当成"没有图谱"
    return pd.Series(
        {key: len(value) for key, value in per_subject.items()}, dtype="float64"
    )


def _note_mismatch(
    issues: list[dict[str, Any]] | None,
    metric: str,
    baseline: pd.Series,
    graph_value: pd.Series,
) -> None:
    """图谱口径与表口径不一致时记一条告警；只报条数，不报患者标识。"""
    if issues is None:
        return
    left = pd.Series(baseline).astype("int64").reset_index(drop=True)
    right = pd.Series(graph_value).astype("int64").reset_index(drop=True)
    if len(left) != len(right):
        differing = max(len(left), len(right))
    else:
        differing = int((left != right).sum())
    if differing:
        issues.append(
            {
                "rule": "LABEL_GRAPH_MISMATCH",
                "severity": "warning",
                "detail": (
                    f"{metric}：图谱口径与表口径在 {differing} 行不一致，已采用图谱口径；"
                    "说明图谱与事件流的过滤条件不同，需确认口径"
                ),
            }
        )


# ---------------------------------------------------------------- 对象视图


def _dataset_frame(events: pd.DataFrame | None) -> pd.DataFrame:
    sensitivity = "L2"
    if events is not None and not events.empty and "sensitivity" in events:
        sensitivity = str(events["sensitivity"].max())
    rows = [
        {
            "object_id": "Dataset:clinical_events",
            "sensitivity": sensitivity,
            "sharing_boundary": "controlled",
        },
        {
            "object_id": "Dataset:patient_360",
            "sensitivity": "L3",
            "sharing_boundary": "aggregate_only",
        },
        {
            "object_id": "Dataset:eventstore",
            "sensitivity": sensitivity,
            "sharing_boundary": "controlled",
        },
    ]
    return pd.DataFrame(rows)


def _patient_frame(
    tables: dict[str, pd.DataFrame],
    events: pd.DataFrame | None,
    notes: pd.DataFrame | None,
    graph: Any = None,
    issues: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    patients = tables["patients"].copy()
    if "subject_id" not in patients:
        return pd.DataFrame(columns=["object_id"])
    patients["subject_id"] = patients["subject_id"].astype("int64")
    patients["object_id"] = "Patient:" + patients["subject_id"].astype(str)

    if "anchor_age" in patients:
        patients["anchor_age"] = pd.to_numeric(patients["anchor_age"], errors="coerce")

    admissions = tables.get("admissions")
    if admissions is not None and "subject_id" in admissions:
        adm = admissions.copy()
        adm["admittime"] = pd.to_datetime(adm["admittime"], errors="coerce")
        adm["dischtime"] = pd.to_datetime(adm["dischtime"], errors="coerce")
        adm["los_days"] = (adm["dischtime"] - adm["admittime"]).dt.total_seconds() / 86400
        agg = adm.groupby("subject_id").agg(
            n_admissions=("hadm_id", "size"),
            total_los_days=("los_days", "sum"),
            mortality=("hospital_expire_flag", "max"),
        )
        patients = patients.merge(agg, on="subject_id", how="left")
    else:
        patients["n_admissions"] = 0
        patients["total_los_days"] = 0.0
        patients["mortality"] = 0

    for column, default in (("n_admissions", 0), ("total_los_days", 0.0), ("mortality", 0)):
        if column not in patients:
            patients[column] = default
    patients["n_admissions"] = patients["n_admissions"].fillna(0).astype(int)
    patients["total_los_days"] = patients["total_los_days"].fillna(0.0).astype(float)
    patients["mortality"] = patients["mortality"].fillna(0).astype(int)

    baseline_burden: pd.Series | None = None
    if events is not None and not events.empty and "subject_id" in events:
        prescriptions = events[events["event_type"] == "Prescription"]
        drugs = (
            prescriptions.groupby("subject_id")["display"].nunique()
            if "display" in prescriptions
            else pd.Series(dtype="int64")
        )
        patients["n_distinct_drugs"] = patients["subject_id"].map(drugs).fillna(0).astype(int)
        diagnoses = events[(events["event_type"] == "Diagnosis") & events["icd_chapter"].notna()]
        chapters = diagnoses.groupby("subject_id")["icd_chapter"].nunique()
        patients["comorbidity_burden"] = patients["subject_id"].map(chapters).fillna(0).astype(int)
        baseline_burden = patients["comorbidity_burden"]
    else:
        patients["n_distinct_drugs"] = 0
        patients["comorbidity_burden"] = 0

    # 共病负担优先按图谱两跳取数（患者→住院→诊断），表口径作为基线用于对账
    graph_chapters = _chapters_by_subject(graph)
    if graph_chapters is not None:
        from_graph = patients["object_id"].map(graph_chapters).fillna(0).astype(int)
        if baseline_burden is not None:
            _note_mismatch(issues, "L4.comorbidity_burden", baseline_burden, from_graph)
        patients["comorbidity_burden"] = from_graph
        patients["comorbidity_source"] = "graph:has_diagnosis"
    else:
        patients["comorbidity_source"] = "events:icd_chapter"

    if notes is not None and not notes.empty and "subject_id" in notes:
        counts = notes.dropna(subset=["subject_id"]).groupby("subject_id").size()
        patients["n_notes"] = patients["subject_id"].map(counts).fillna(0).astype(int)
    else:
        patients["n_notes"] = 0

    patients["drug_intensity"] = (
        patients["n_distinct_drugs"] / patients["total_los_days"].clip(lower=0.1)
    ).round(4)
    patients["sensitivity"] = "L3"
    return patients


def _encounter_frame(
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame | None,
    patient_flags: pd.DataFrame,
    graph: Any = None,
    issues: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    encounters = tables["admissions"].copy()
    encounters["admittime"] = pd.to_datetime(encounters["admittime"], errors="coerce")
    encounters["dischtime"] = pd.to_datetime(encounters["dischtime"], errors="coerce")
    encounters["los_days"] = (
        encounters["dischtime"] - encounters["admittime"]
    ).dt.total_seconds() / 86400
    encounters["object_id"] = "Encounter:" + encounters["hadm_id"].astype(str)

    for table_name, relation, feature in (
        ("prescriptions", "has_prescription", "n_prescriptions"),
        ("diagnoses_icd", "has_diagnosis", "n_diagnoses"),
        ("procedures_icd", "has_procedure", "n_procedures"),
    ):
        frame = tables.get(table_name)
        baseline = (
            encounters["hadm_id"].map(frame.groupby("hadm_id").size()).fillna(0).astype(int)
            if frame is not None
            else None
        )
        # 优先按图谱边表计数（每条边就是一个"该住院下有该事件"的事实）
        counts = _edge_counts(graph, relation)
        if counts is None:
            encounters[feature] = baseline if baseline is not None else 0
        else:
            from_graph = encounters["object_id"].map(counts).fillna(0).astype(int)
            if baseline is not None:
                _note_mismatch(issues, feature, baseline, from_graph)
            encounters[feature] = from_graph

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
    icu["object_id"] = "ICUStay:" + icu["stay_id"].astype(str)
    return icu


def _event_frame(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["event_time"] = pd.to_datetime(frame["event_time"], errors="coerce")
    frame["time_precision"] = "datetime"
    frame.loc[frame["source_table"].eq("procedures_icd"), "time_precision"] = "date"
    frame.loc[frame["event_time"].isna(), "time_precision"] = "null"

    frame["missing_reason"] = None
    frame.loc[frame["event_time"].isna(), "missing_reason"] = "no_source_field"
    numeric_missing = (
        frame["event_time"].notna()
        & frame["numeric_value"].isna()
        & frame["event_type"].eq("Prescription")
    )
    frame.loc[numeric_missing, "missing_reason"] = "non_numeric"
    frame["object_id"] = "Event:" + frame["record_id"].astype(str)
    frame["sensitivity"] = frame["sensitivity"].fillna("L2")
    return frame


def _merge_concept_rows(concepts: pd.DataFrame) -> pd.DataFrame:
    """同一概念（同一 CUI）下的多个编码合并成一行对象。

    合并后 ``code_system`` 变成多值（``ICD9|ICD10``），因为一个 UMLS 概念本来就可能
    由多套术语的多个编码表达；``semantic_type`` 取第一个非 ``Unknown`` 的值。
    """
    concepts = concepts.copy()
    concepts["semantic_type"] = concepts["semantic_type"].fillna("Unknown")

    def _join_systems(values: pd.Series) -> str:
        return "|".join(sorted({str(value) for value in values if pd.notna(value)}))

    def _first_known(values: pd.Series) -> str:
        for value in values:
            text = str(value)
            if text and text != "Unknown" and text != "nan":
                return text
        return "Unknown"

    def _first_cui(values: pd.Series) -> str | None:
        for value in values:
            if pd.notna(value):
                return str(value)
        return None

    return concepts.groupby("object_id", as_index=False, sort=True).agg(
        code_system=("code_system", _join_systems),
        semantic_type=("semantic_type", _first_known),
        cui=("cui", _first_cui),
    )


def _concept_frame(
    events: pd.DataFrame,
    concept_map: pd.DataFrame | None,
) -> pd.DataFrame:
    concepts = events[["code_system", "code", "icd_chapter"]].dropna(
        subset=["code_system", "code"]
    ).drop_duplicates()
    concepts["semantic_type"] = concepts["icd_chapter"].fillna("Unknown")

    if concept_map is not None and not concept_map.empty:
        mapping = concept_map.rename(
            columns={
                "source_system": "code_system",
                "source_code": "code",
                "sty": "umls_sty",
            }
        )
        keep = [column for column in ("code_system", "code", "cui", "umls_sty") if column in mapping.columns]
        mapping = mapping[keep].drop_duplicates(["code_system", "code"])
        concepts = concepts.merge(mapping, on=["code_system", "code"], how="left")
        if "umls_sty" in concepts:
            concepts["semantic_type"] = concepts["umls_sty"].fillna(concepts["icd_chapter"]).fillna("Unknown")
    if "cui" not in concepts:
        concepts["cui"] = None

    # 主键与图谱 Concept 节点用同一个函数：有 CUI 用 CUI，否则用 编码体系:编码
    concepts = concepts[concepts["code"].astype(str).str.strip() != ""]
    concepts["object_id"] = [
        concept_object_id(system, code, cui)
        for system, code, cui in zip(concepts["code_system"], concepts["code"], concepts["cui"])
    ]
    if concepts["object_id"].duplicated().any():
        concepts = _merge_concept_rows(concepts)
    return concepts.drop_duplicates("object_id")


# ---------------------------------------------------------------- 标签收集


def _row(
    object_id: str,
    definition: LabelDefinition,
    value: str,
    source: str,
    evidence: str,
    version: str,
    now: str,
) -> dict[str, Any]:
    return {
        "object_type": definition.object,
        "object_id": object_id,
        "tag_key": definition.key,
        "tag_value": value,
        "layer": definition.layer,
        "source": source,
        "confidence": 1.0,
        "evidence": evidence,
        "version": version,
        "computed_at": now,
    }


def _collect(
    frame: pd.DataFrame,
    definitions: list[LabelDefinition],
    object_type: str,
    version: str,
    now: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if frame.empty or "object_id" not in frame:
        return rows
    for definition in definitions:
        if definition.object != object_type:
            continue
        if definition.label_type == "field":
            column = definition.field or ""
            if column not in frame:
                continue
            series = frame[column]
            for index in frame.index[series.notna()]:
                value = series.loc[index]
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                rows.append(
                    _row(
                        str(frame.at[index, "object_id"]),
                        definition,
                        _as_text(value),
                        "attribute",
                        f"field:{column}",
                        version,
                        now,
                    )
                )
        elif definition.label_type == "constant":
            value = _as_text(definition.value)
            for object_id in frame["object_id"].dropna():
                rows.append(
                    _row(
                        str(object_id),
                        definition,
                        value,
                        "definition",
                        "constant",
                        version,
                        now,
                    )
                )
        else:
            mask = _eval_rule(frame, definition)
            frame[f"label_{_safe(definition.key)}"] = mask
            value = _as_text(definition.value)
            for object_id in frame.loc[mask, "object_id"].dropna():
                rows.append(
                    _row(
                        str(object_id),
                        definition,
                        value,
                        "rule",
                        f"rule:{definition.rule}",
                        version,
                        now,
                    )
                )
    return rows


def compute_labels(
    tables: dict[str, pd.DataFrame],
    notes: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
    concept_map: pd.DataFrame | None = None,
    manual_path: Path | str | None = None,
    definitions: list[LabelDefinition] | None = None,
    version: str | None = None,
    graph: Any = None,
    issues: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """计算对象标签；manual_path 提供的手动标签会覆盖同名断言。

    ``graph``（``graph.build.GraphData``）非空时，住院级条目数与共病负担按图谱取数，
    并把与表口径的差异写进 ``issues``（``LABEL_GRAPH_MISMATCH``）。
    """
    if definitions is None or version is None:
        loaded_version, loaded = load_definitions()
        definitions = definitions or loaded
        version = version or loaded_version
    now = datetime.now(timezone.utc).isoformat()

    dataset = _dataset_frame(events)
    dataset_rows = _collect(dataset, definitions, "Dataset", version, now)

    patients = _patient_frame(tables, events, notes, graph=graph, issues=issues)
    patient_rows = _collect(patients, definitions, "Patient", version, now)
    patient_flag_cols = [
        f"label_{_safe(d.key)}"
        for d in definitions
        if d.object == "Patient" and d.label_type not in {"field", "constant"}
    ]
    patient_flags = patients[["subject_id"] + patient_flag_cols].rename(
        columns={column: f"patient_{column[len('label_'):]}" for column in patient_flag_cols}
    )

    encounters = _encounter_frame(tables, notes, patient_flags, graph=graph, issues=issues)
    encounter_rows = _collect(encounters, definitions, "Encounter", version, now)
    encounter_flag_cols = [
        f"label_{_safe(d.key)}"
        for d in definitions
        if d.object == "Encounter" and d.label_type not in {"field", "constant"}
    ]
    encounter_flags = encounters[["hadm_id"] + encounter_flag_cols].rename(
        columns={column: f"encounter_{column[len('label_'):]}" for column in encounter_flag_cols}
    )

    icu = _icu_frame(tables, encounter_flags)
    icu_rows = _collect(icu, definitions, "ICUStay", version, now)

    event_rows: list[dict[str, Any]] = []
    concept_rows: list[dict[str, Any]] = []
    if events is not None and not events.empty:
        event_frame = _event_frame(events)
        event_rows = _collect(event_frame, definitions, "Event", version, now)
        concept_frame = _concept_frame(events, concept_map)
        concept_rows = _collect(concept_frame, definitions, "Concept", version, now)

    auto = pd.DataFrame(
        dataset_rows + patient_rows + encounter_rows + icu_rows + event_rows + concept_rows,
        columns=OUTPUT_COLUMNS,
    )

    manual = _read_manual(manual_path, definitions, version, now)
    if manual is not None and not manual.empty:
        keys = set(zip(manual["object_type"], manual["object_id"], manual["tag_key"]))
        if not auto.empty:
            mask = [
                (row.object_type, row.object_id, row.tag_key) not in keys
                for row in auto.itertuples()
            ]
            auto = auto[mask]
        auto = pd.concat([auto, manual], ignore_index=True)

    if auto.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return auto.sort_values(["object_type", "object_id", "tag_key"]).reset_index(drop=True)


def _read_manual(
    path: Path | str | None,
    definitions: list[LabelDefinition],
    version: str,
    now: str,
) -> pd.DataFrame | None:
    if path is None:
        return None
    manual_path = Path(path)
    if not manual_path.exists():
        raise FileNotFoundError(f"手动标签文件不存在: {manual_path}")
    frame = pd.read_csv(manual_path, dtype=str)
    required = {"object_type", "object_id"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"手动标签缺少列: {', '.join(sorted(missing))}")
    key_column = "tag_key" if "tag_key" in frame.columns else "key"
    value_column = "tag_value" if "tag_value" in frame.columns else "value"
    if key_column not in frame.columns or value_column not in frame.columns:
        raise ValueError("手动标签需要 tag_key/tag_value（或兼容 key/value）")

    layer_by_key = {
        (definition.key, definition.object): definition.layer for definition in definitions
    }
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        tag_key = str(row[key_column])
        object_type = str(row["object_type"])
        rows.append(
            {
                "object_type": object_type,
                "object_id": str(row["object_id"]),
                "tag_key": tag_key,
                "tag_value": str(row[value_column]),
                "layer": layer_by_key.get((tag_key, object_type), "L4"),
                "source": "manual",
                "confidence": 1.0,
                "evidence": "manual",
                "version": version,
                "computed_at": now,
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
