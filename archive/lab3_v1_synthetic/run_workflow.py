"""实验三：基于 LangGraph 的医疗数据对象到 MEDS 的治理工作流。

运行：python run_workflow.py
输出写入本目录 output/。输入为脚本内定义的、与 MIMIC-IV 常见表结构一致的模拟数据，
不包含真实患者信息。
"""

from __future__ import annotations

import json
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
MEDS_SOURCE = Path(__file__).resolve().parents[2] / "third_party" / "meds" / "src"
if MEDS_SOURCE.exists():
    sys.path.insert(0, str(MEDS_SOURCE))


class WorkflowState(TypedDict, total=False):
    raw_tables: dict[str, pd.DataFrame]
    objects: dict[str, pd.DataFrame]
    candidates: list[dict[str, Any]]
    events: list[dict[str, Any]]
    issues: list[dict[str, str]]
    audit: list[dict[str, str]]
    validator: str
    output_files: list[str]


def audit(state: WorkflowState, agent: str, message: str) -> list[dict[str, str]]:
    return state.get("audit", []) + [{"agent": agent, "message": message}]


def issue(rule: str, severity: str, source: str, record_id: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "severity": severity, "source": source, "record_id": record_id, "detail": detail}


def read_data(_: WorkflowState) -> WorkflowState:
    """数据读取 Agent：模拟两家医院的患者、就诊、诊断、用药、检验和文本表。"""
    tables = {
        "patients": pd.DataFrame([
            ["HOSP_A", "P001", "F", "1980-05-08"], ["HOSP_A", "P002", "M", "1972-11-18"],
            ["HOSP_B", "P003", "F", "1991-02-14"], ["HOSP_B", "P004", "M", "1963-08-22"],
        ], columns=["site_id", "patient_id", "gender", "birth_date"]),
        "encounters": pd.DataFrame([
            ["HOSP_A", "E001", "P001", "2026-03-01 08:20", "2026-03-04 10:00", "ICU"],
            ["HOSP_A", "E002", "P002", "2026-03-02 09:10", "2026-03-03 16:30", "WARD"],
            ["HOSP_B", "E003", "P003", "2026-03-05 11:00", "2026-03-08 09:00", "ICU"],
            ["HOSP_B", "E004", "P004", "2026-03-06 14:30", "2026-03-05 12:00", "ICU"],
        ], columns=["site_id", "encounter_id", "patient_id", "admit_time", "discharge_time", "care_unit"]),
        "diagnoses": pd.DataFrame([
            ["D001", "E001", "P001", "I10", "2026-03-01 09:00"], ["D002", "E002", "P002", "E11.9", "2026-03-02 10:00"],
            ["D003", "E003", "P003", "J18.9", "2026-03-05 12:00"], ["D004", "E999", "P004", "I10", "2026-03-06 15:00"],
        ], columns=["diagnosis_id", "encounter_id", "patient_id", "icd10_code", "diagnosis_time"]),
        "medications": pd.DataFrame([
            ["M001", "E001", "P001", "RXNORM:860975", "2026-03-01 10:30"], ["M002", "E003", "P003", "RXNORM:197361", "2026-03-05 13:00"],
            ["M003", "E003", "P003", "LOCAL:UNKNOWN", "2026-03-06 08:00"],
        ], columns=["medication_id", "encounter_id", "patient_id", "drug_code", "start_time"]),
        "labs": pd.DataFrame([
            ["L001", "E001", "P001", "LOINC:2160-0", 3.9, "mmol/L", "2026-03-01 11:00"],
            ["L002", "E002", "P002", "LOINC:718-7", 13.2, "g/dL", "2026-03-02 11:30"],
            ["L003", "E003", "P003", "LOINC:2160-0", 9999.0, "mmol/L", "2026-03-05 14:00"],
            ["L003", "E003", "P003", "LOINC:2160-0", 9999.0, "mmol/L", "2026-03-05 14:00"],
        ], columns=["lab_id", "encounter_id", "patient_id", "loinc_code", "value", "unit", "lab_time"]),
        "notes": pd.DataFrame([
            ["N001", "E001", "P001", "2026-03-01 15:00", "admission_note", "Hypertension history; potassium replacement started."],
            ["N002", "E003", "P003", "", "discharge_summary", "Pneumonia improved after antibiotic treatment."],
        ], columns=["note_id", "encounter_id", "patient_id", "note_time", "note_type", "note_text"]),
    }
    return {"raw_tables": tables, "issues": [], "audit": audit({}, "数据读取 Agent", "读取 6 张模拟源表；数据按医院节点本地处理。")}


def extract_objects(state: WorkflowState) -> WorkflowState:
    """对象提取 Agent：建立患者、就诊和诊疗事件候选对象。"""
    raw = state["raw_tables"]
    patients = raw["patients"].copy()
    patients["subject_id"] = pd.Series([1001, 1002, 2001, 2002], dtype="int64")
    encounters = raw["encounters"].copy()
    candidates: list[dict[str, Any]] = []
    for _, row in raw["diagnoses"].iterrows():
        candidates.append({"kind": "diagnosis", "record_id": row.diagnosis_id, **row.to_dict()})
    for _, row in raw["medications"].iterrows():
        candidates.append({"kind": "medication", "record_id": row.medication_id, **row.to_dict()})
    for _, row in raw["labs"].iterrows():
        candidates.append({"kind": "lab", "record_id": row.lab_id, **row.to_dict()})
    for _, row in raw["notes"].iterrows():
        candidates.append({"kind": "note", "record_id": row.note_id, **row.to_dict()})
    return {"objects": {"patients": patients, "encounters": encounters}, "candidates": candidates,
            "audit": audit(state, "对象提取 Agent", f"生成 {len(patients)} 个患者对象、{len(encounters)} 个就诊对象和 {len(candidates)} 个诊疗候选事件。")}


def relate_objects(state: WorkflowState) -> WorkflowState:
    """关联 Agent：校验患者和就诊关联；不臆测补全无效关联或缺失时间。"""
    patients, encounters = state["objects"]["patients"], state["objects"]["encounters"]
    valid_patients = set(patients.patient_id)
    valid_encounters = set(encounters.encounter_id)
    issues = list(state["issues"])
    usable: list[dict[str, Any]] = []
    for candidate in state["candidates"]:
        if candidate["patient_id"] not in valid_patients:
            issues.append(issue("OBJECT_ASSOCIATION", "error", candidate["kind"], candidate["record_id"], "patient_id 未在患者对象中找到"))
            continue
        if candidate["encounter_id"] not in valid_encounters:
            issues.append(issue("OBJECT_ASSOCIATION", "error", candidate["kind"], candidate["record_id"], "encounter_id 未在就诊对象中找到，未输出该事件"))
            continue
        if candidate["kind"] == "note" and not str(candidate["note_time"]).strip():
            issues.append(issue("MISSING_TIME", "warning", "note", candidate["record_id"], "文本记录缺少事件时间；未将其伪造为就诊时间"))
            continue
        usable.append(candidate)
    for _, encounter in encounters.iterrows():
        if pd.Timestamp(encounter.discharge_time) < pd.Timestamp(encounter.admit_time):
            issues.append(issue("TEMPORAL_CONSISTENCY", "error", "encounter", encounter.encounter_id, "出院时间早于入院时间"))
    return {"candidates": usable, "issues": issues,
            "audit": audit(state, "关联 Agent", f"保留 {len(usable)} 个可关联候选事件；发现 {len(issues)} 项关联或时间问题。")}


def map_to_meds(state: WorkflowState) -> WorkflowState:
    """MEDS 映射 Agent：将对象映射为患者维度事件及可追溯扩展字段。"""
    patients = state["objects"]["patients"]
    patient_map = dict(zip(patients.patient_id, patients.subject_id))
    site_map = dict(zip(patients.patient_id, patients.site_id))
    issues = list(state["issues"])
    events: list[dict[str, Any]] = []
    for _, person in patients.iterrows():
        events.extend([
            {"subject_id": person.subject_id, "time": None, "code": f"GENDER//{person.gender}", "numeric_value": None, "text_value": None,
             "encounter_id": None, "site_id": person.site_id, "source_table": "patients", "source_record_id": person.patient_id, "mapping_version": "v1.0"},
            {"subject_id": person.subject_id, "time": person.birth_date, "code": "MEDS_BIRTH", "numeric_value": None, "text_value": None,
             "encounter_id": None, "site_id": person.site_id, "source_table": "patients", "source_record_id": person.patient_id, "mapping_version": "v1.0"},
        ])
    mapping = {
        "diagnosis": ("diagnosis_time", lambda x: f"DIAGNOSIS//ICD10//{x['icd10_code']}", None),
        "medication": ("start_time", lambda x: f"MEDICATION//{x['drug_code']}", None),
        "lab": ("lab_time", lambda x: f"LAB//{x['loinc_code']}//{x['unit']}", "value"),
        "note": ("note_time", lambda x: f"NOTE//{x['note_type'].upper()}", "note_text"),
    }
    for item in state["candidates"]:
        time_key, code_fn, value_key = mapping[item["kind"]]
        if item["kind"] == "medication" and item["drug_code"].startswith("LOCAL:"):
            issues.append(issue("CODE_MAPPING", "error", "medication", item["record_id"], "本地药物编码无批准映射，未输出该事件"))
            continue
        numeric_value = float(item[value_key]) if item["kind"] == "lab" else None
        text_value = item[value_key] if item["kind"] == "note" else None
        source_table = {"diagnosis": "diagnoses", "medication": "medications", "lab": "labs", "note": "notes"}[item["kind"]]
        events.append({"subject_id": int(patient_map[item["patient_id"]]), "time": item[time_key], "code": code_fn(item),
                       "numeric_value": numeric_value, "text_value": text_value, "encounter_id": item["encounter_id"],
                       "site_id": site_map[item["patient_id"]], "source_table": source_table, "source_record_id": item["record_id"], "mapping_version": "v1.0"})
    return {"events": events, "issues": issues,
            "audit": audit(state, "MEDS 映射 Agent", f"生成 {len(events)} 条 MEDS 候选事件，并保留来源表、来源记录和映射版本。")}


def validate_events(state: WorkflowState) -> WorkflowState:
    """质量校验 Agent：检查 MEDS 核心字段、重复和临床取值范围。"""
    events = state["events"]
    issues = list(state["issues"])
    seen: set[tuple[Any, ...]] = set()
    valid: list[dict[str, Any]] = []
    for event in events:
        record_key = event["source_record_id"]
        if event["subject_id"] is None or not event["code"]:
            issues.append(issue("MEDS_REQUIRED_FIELD", "error", event["source_table"], str(record_key), "subject_id 或 code 为空"))
            continue
        dedupe_key = (event["subject_id"], event["time"], event["code"], event["numeric_value"], event["source_record_id"])
        if dedupe_key in seen:
            issues.append(issue("DUPLICATE_EVENT", "warning", event["source_table"], str(record_key), "重复源记录，仅保留第一条"))
            continue
        seen.add(dedupe_key)
        if event["code"].startswith("LAB//") and event["numeric_value"] is not None and not 0 <= event["numeric_value"] <= 1000:
            issues.append(issue("VALUE_RANGE", "warning", event["source_table"], str(record_key), "检验数值超出本演示规则 0-1000，保留并标记供人工复核"))
        valid.append(event)
    validator = "basic PyArrow checks"
    try:
        from meds import DataSchema  # type: ignore
    except ImportError:
        # 助教源码未安装为 wheel 时，直接加载其 Schema 定义；不复制或改写 Schema。
        schema_path = MEDS_SOURCE / "meds" / "schema.py"
        spec = importlib.util.spec_from_file_location("meds_schema_local", schema_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load MEDS schema from {schema_path}")
        schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema_module)
        DataSchema = schema_module.DataSchema
    core_frame = pd.DataFrame([{k: item[k] for k in ("subject_id", "time", "code", "numeric_value", "text_value")} for item in valid])
    core_frame["time"] = pd.to_datetime(core_frame["time"], format="mixed", errors="coerce")
    core_frame["numeric_value"] = pd.to_numeric(core_frame["numeric_value"], errors="coerce").astype("float32")
    core = pa.Table.from_pandas(core_frame, schema=pa.schema([
        pa.field("subject_id", pa.int64(), nullable=False), pa.field("time", pa.timestamp("us")),
        pa.field("code", pa.string(), nullable=False), pa.field("numeric_value", pa.float32()),
        pa.field("text_value", pa.large_string()),
    ]), preserve_index=False)
    DataSchema.validate(core)
    validator = "MEDS DataSchema.validate"
    return {"events": valid, "issues": issues, "validator": validator,
            "audit": audit(state, "质量校验 Agent", f"通过核心字段/重复/范围校验，保留 {len(valid)} 条事件；校验器：{validator}。")}


def write_outputs(state: WorkflowState) -> WorkflowState:
    """结果汇总 Agent：写 MEDS 数据、编码元数据、审计链路和质量报告。"""
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "data").mkdir(exist_ok=True)
    (OUTPUT / "metadata").mkdir(exist_ok=True)
    frame = pd.DataFrame(state["events"])
    # 源表同时含 YYYY-MM-DD 和 YYYY-MM-DD HH:MM；使用 mixed 避免整列按首个格式误解析。
    frame["time"] = pd.to_datetime(frame["time"], format="mixed", errors="coerce")
    frame = frame.sort_values(["subject_id", "time"], na_position="first", kind="stable")
    table = pa.Table.from_pandas(frame, preserve_index=False).cast(pa.schema([
        pa.field("subject_id", pa.int64(), nullable=False), pa.field("time", pa.timestamp("us")), pa.field("code", pa.string(), nullable=False),
        pa.field("numeric_value", pa.float32()), pa.field("text_value", pa.string()), pa.field("encounter_id", pa.string()), pa.field("site_id", pa.string()),
        pa.field("source_table", pa.string()), pa.field("source_record_id", pa.string()), pa.field("mapping_version", pa.string()),
    ]))
    data_path = OUTPUT / "data" / "events.parquet"
    pq.write_table(table, data_path)
    descriptions = {"MEDS_BIRTH": "Birth date", "GENDER//F": "Female", "GENDER//M": "Male"}
    codes = sorted(frame.code.unique())
    code_table = pa.table({"code": codes, "description": [descriptions.get(c, c) for c in codes], "parent_codes": [[] for _ in codes]})
    pq.write_table(code_table, OUTPUT / "metadata" / "codes.parquet")
    metadata = {"dataset_name": "Lab3 simulated multi-site EMR", "dataset_version": "1.0", "etl_name": "LangGraph medical governance workflow", "etl_version": "1.0", "meds_version": "0.4", "created_at": datetime.now(timezone.utc).isoformat(), "raw_source_id_columns": ["encounter_id", "source_record_id"], "site_id_columns": ["site_id"], "other_extension_columns": ["source_table", "mapping_version"]}
    (OUTPUT / "metadata" / "dataset.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    final_audit = audit(state, "结果汇总 Agent", f"输出 {len(frame)} 条排序后的事件和质量报告。")
    pd.DataFrame(state["issues"]).to_csv(OUTPUT / "quality_issues.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(final_audit).to_csv(OUTPUT / "audit_log.csv", index=False, encoding="utf-8-sig")
    summary = {"input_tables": 6, "output_events": len(frame), "unique_subjects": int(frame.subject_id.nunique()), "unique_codes": len(codes), "quality_issues": len(state["issues"]), "errors": sum(x["severity"] == "error" for x in state["issues"]), "warnings": sum(x["severity"] == "warning" for x in state["issues"]), "validator": state["validator"]}
    (OUTPUT / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_files": [str(data_path), str(OUTPUT / "metadata" / "codes.parquet"), str(OUTPUT / "quality_issues.csv"), str(OUTPUT / "quality_summary.json")],
            "audit": final_audit}


NODES = [("read_data", read_data), ("extract_objects", extract_objects), ("relate_objects", relate_objects), ("map_to_meds", map_to_meds), ("validate_events", validate_events), ("write_outputs", write_outputs)]


def build_graph() -> tuple[Any, bool]:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None, False
    graph = StateGraph(WorkflowState)
    for name, node in NODES:
        graph.add_node(name, node)
    graph.add_edge(START, NODES[0][0])
    for (current, _), (following, _) in zip(NODES, NODES[1:]):
        graph.add_edge(current, following)
    graph.add_edge(NODES[-1][0], END)
    return graph.compile(), True


def main() -> None:
    graph, uses_langgraph = build_graph()
    if uses_langgraph:
        final_state = graph.invoke({})
        engine = "LangGraph StateGraph"
    else:
        final_state: WorkflowState = {}
        for _, node in NODES:
            final_state.update(node(final_state))
        engine = "sequential compatibility runner (install langgraph to use StateGraph)"
    print(f"Engine: {engine}")
    print(json.dumps(json.loads((OUTPUT / "quality_summary.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    print("Output:")
    print("\n".join(final_state["output_files"]))


if __name__ == "__main__":
    main()
