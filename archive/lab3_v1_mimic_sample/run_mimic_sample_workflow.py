"""实验三：使用本地 MIMIC-IV v3.1 小样本的 LangGraph -> MEDS 工作流。

只读取经授权的本地文件。脚本不会复制原始 CSV，不读取临床文本，输出采用重新编号的
subject_id，且不保存 MIMIC 原始 subject_id / hadm_id。请勿将 output_mimic_sample/ 打包或外传。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from langgraph.graph import END, START, StateGraph

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(
    os.environ.get(
        "MIMIC_ROOT",
        str(Path(__file__).resolve().parents[2] / "data" / "mimic-iv-3.1"),
    )
)
OUTPUT = ROOT / "output_mimic_sample"
MEDS_SOURCE = Path(__file__).resolve().parents[2] / "third_party" / "meds" / "src"
COHORT_SIZE = 20
LAB_SCAN_ROWS = 300_000


class State(TypedDict, total=False):
    tables: dict[str, pd.DataFrame]
    cohort: pd.DataFrame
    events: list[dict[str, Any]]
    issues: list[dict[str, str]]
    audit: list[dict[str, str]]
    validator: str
    output_files: list[str]


def add_audit(state: State, agent: str, message: str) -> list[dict[str, str]]:
    return state.get("audit", []) + [{"agent": agent, "message": message}]


def add_issue(rule: str, severity: str, source: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "severity": severity, "source": source, "detail": detail}


def csv_path(group: str, name: str) -> Path:
    path = DATA_ROOT / group / f"{name}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"MIMIC-IV file not found: {path}")
    return path


def read_data(_: State) -> State:
    """读取 Agent：构建确定性 ICU 队列，并仅保留与队列有关的表行。"""
    icu = pd.read_csv(csv_path("icu", "icustays"), usecols=["subject_id", "hadm_id", "stay_id", "first_careunit", "intime", "outtime"])
    admissions = pd.read_csv(csv_path("hosp", "admissions"), usecols=["subject_id", "hadm_id", "admittime", "dischtime", "hospital_expire_flag"])
    patients = pd.read_csv(csv_path("hosp", "patients"), usecols=["subject_id", "gender", "anchor_age"])

    # 对大表只读取固定前缀；在其中选择有 ICU stay 的患者，避免全量载入 2.47 GB labevents。
    labs_prefix = pd.read_csv(
        csv_path("hosp", "labevents"),
        nrows=LAB_SCAN_ROWS,
        usecols=["labevent_id", "subject_id", "hadm_id", "itemid", "charttime", "valuenum", "valueuom", "flag"],
    )
    icu_subjects = set(icu["subject_id"].dropna().astype("int64"))
    candidate_subjects = labs_prefix.loc[labs_prefix["subject_id"].isin(icu_subjects), "subject_id"].drop_duplicates().astype("int64")
    sample_subjects = candidate_subjects.iloc[:COHORT_SIZE].tolist()
    if len(sample_subjects) < COHORT_SIZE:
        raise RuntimeError(f"Only found {len(sample_subjects)} ICU subjects in the bounded laboratory scan")

    cohort_ids = {subject_id: index + 1 for index, subject_id in enumerate(sample_subjects)}
    cohort = pd.DataFrame({"raw_subject_id": sample_subjects, "subject_id": list(range(1, len(sample_subjects) + 1))})
    admissions = admissions[admissions["subject_id"].isin(sample_subjects)].copy()
    patients = patients[patients["subject_id"].isin(sample_subjects)].copy()
    icu = icu[icu["subject_id"].isin(sample_subjects)].copy()
    labs = labs_prefix[labs_prefix["subject_id"].isin(sample_subjects)].copy()

    diagnoses = pd.read_csv(csv_path("hosp", "diagnoses_icd"), usecols=["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version"])
    diagnoses = diagnoses[diagnoses["subject_id"].isin(sample_subjects)].copy()

    prescription_parts: list[pd.DataFrame] = []
    remaining = set(sample_subjects)
    for chunk in pd.read_csv(
        csv_path("hosp", "prescriptions"),
        usecols=["subject_id", "hadm_id", "pharmacy_id", "starttime", "formulary_drug_cd", "route"],
        chunksize=100_000,
    ):
        hit = chunk[chunk["subject_id"].isin(sample_subjects)]
        if not hit.empty:
            prescription_parts.append(hit)
            remaining -= set(hit["subject_id"].dropna().astype("int64"))
        # 文件按 subject_id 排序；样本来自前缀时经过样本最大 ID 即可安全停止。
        if pd.to_numeric(chunk["subject_id"], errors="coerce").min() > max(sample_subjects):
            break
    prescriptions = pd.concat(prescription_parts, ignore_index=True) if prescription_parts else pd.DataFrame(columns=["subject_id", "hadm_id", "pharmacy_id", "starttime", "formulary_drug_cd", "route"])

    tables = {"patients": patients, "admissions": admissions, "icustays": icu, "diagnoses_icd": diagnoses, "prescriptions": prescriptions, "labevents": labs}
    message = f"从真实 MIMIC-IV 本地文件抽取 {len(sample_subjects)} 名 ICU 患者；读取 6 张关联结构化表，检验扫描上限为 {LAB_SCAN_ROWS:,} 行。"
    return {"tables": tables, "cohort": cohort, "issues": [], "audit": add_audit({}, "数据读取 Agent", message)}


def extract_objects(state: State) -> State:
    """对象 Agent：建立重新编号患者与住院/ICU 就诊对象，不将原始标识写入输出。"""
    tables, cohort = state["tables"], state["cohort"]
    raw_to_subject = dict(zip(cohort.raw_subject_id, cohort.subject_id))
    patients = tables["patients"].copy()
    patients["subject_id"] = patients["subject_id"].map(raw_to_subject).astype("int64")
    encounters = tables["admissions"].copy()
    encounters["subject_id"] = encounters["subject_id"].map(raw_to_subject).astype("int64")
    return {"tables": {**tables, "patients": patients, "admissions": encounters},
            "audit": add_audit(state, "对象提取 Agent", f"建立 {len(patients)} 个患者对象、{len(encounters)} 个住院对象和 {len(tables['icustays'])} 个 ICU 就诊对象。")}


def map_to_meds(state: State) -> State:
    """映射 Agent：将真实结构化记录映射为 MEDS 事件，不写出原始患者或住院标识。"""
    tables, cohort = state["tables"], state["cohort"]
    raw_to_subject = dict(zip(cohort.raw_subject_id, cohort.subject_id))
    events: list[dict[str, Any]] = []
    issues = list(state["issues"])

    def event(subject_id: int, time: Any, code: str, value: float | None, source: str) -> None:
        events.append({"subject_id": int(subject_id), "time": time, "code": code, "numeric_value": value, "text_value": None,
                       "source_table": source, "mapping_version": "mimic-iv-v3.1-to-meds-v1"})

    for _, row in tables["patients"].iterrows():
        event(row.subject_id, None, f"GENDER//{row.gender}", None, "patients")
        if pd.notna(row.anchor_age):
            event(row.subject_id, None, "DEMOGRAPHIC//ANCHOR_AGE", float(row.anchor_age), "patients")

    for _, row in tables["admissions"].iterrows():
        event(row.subject_id, row.admittime, "ADMISSION//HOSPITAL", None, "admissions")
        if pd.notna(row.dischtime):
            event(row.subject_id, row.dischtime, "DISCHARGE//HOSPITAL", None, "admissions")

    for _, row in tables["icustays"].iterrows():
        subject_id = raw_to_subject[int(row.subject_id)]
        event(subject_id, row.intime, f"ICU_STAY//{row.first_careunit}", None, "icustays")
        if pd.notna(row.outtime):
            event(subject_id, row.outtime, "ICU_DISCHARGE", None, "icustays")

    missing_dx_time = 0
    for _, row in tables["diagnoses_icd"].iterrows():
        if pd.isna(row.icd_code):
            continue
        subject_id = raw_to_subject[int(row.subject_id)]
        event(subject_id, None, f"DIAGNOSIS//ICD{int(row.icd_version)}//{row.icd_code}", None, "diagnoses_icd")
        missing_dx_time += 1
    if missing_dx_time:
        issues.append(add_issue("SOURCE_TIME_LIMITATION", "warning", "diagnoses_icd", f"{missing_dx_time} 条诊断记录在源表中没有事件时间；按 MEDS 允许规则保留空 time，未以住院时间补写。"))

    skipped_medications = 0
    for _, row in tables["prescriptions"].iterrows():
        if pd.isna(row.formulary_drug_cd) or not str(row.formulary_drug_cd).strip():
            skipped_medications += 1
            continue
        subject_id = raw_to_subject[int(row.subject_id)]
        event(subject_id, row.starttime, f"MEDICATION//MIMIC_FORMULARY//{str(row.formulary_drug_cd).strip()}", None, "prescriptions")
    if skipped_medications:
        issues.append(add_issue("CODE_MAPPING", "warning", "prescriptions", f"{skipped_medications} 条处方缺少 formulary_drug_cd，未输出为 MEDS 事件。"))

    skipped_labs = 0
    for _, row in tables["labevents"].iterrows():
        if pd.isna(row.itemid) or pd.isna(row.charttime):
            skipped_labs += 1
            continue
        subject_id = raw_to_subject[int(row.subject_id)]
        unit = "UNKNOWN" if pd.isna(row.valueuom) else str(row.valueuom).strip().replace(" ", "_")
        numeric_value = None if pd.isna(row.valuenum) else float(row.valuenum)
        event(subject_id, row.charttime, f"LAB//MIMIC_ITEM//{int(row.itemid)}//{unit}", numeric_value, "labevents")
    if skipped_labs:
        issues.append(add_issue("REQUIRED_FIELD", "warning", "labevents", f"{skipped_labs} 条检验记录缺少 itemid 或 charttime，未输出。"))

    return {"events": events, "issues": issues,
            "audit": add_audit(state, "MEDS 映射 Agent", f"由真实结构化记录生成 {len(events)} 条 MEDS 候选事件；原始 subject_id 和 hadm_id 未写入事件数据。")}


def validate_events(state: State) -> State:
    """质量 Agent：执行去重、核心字段检查和助教提供的 MEDS Schema 验证。"""
    issues, valid, seen = list(state["issues"]), [], set()
    duplicate_count = 0
    for item in state["events"]:
        if not item["code"] or item["subject_id"] is None:
            issues.append(add_issue("MEDS_REQUIRED_FIELD", "error", item["source_table"], "subject_id 或 code 为空，记录被阻断。"))
            continue
        key = (item["subject_id"], item["time"], item["code"], item["numeric_value"])
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        valid.append(item)

    schema_path = MEDS_SOURCE / "meds" / "schema.py"
    spec = importlib.util.spec_from_file_location("meds_schema_local", schema_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load MEDS schema from {schema_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = pd.DataFrame([{key: item[key] for key in ("subject_id", "time", "code", "numeric_value", "text_value")} for item in valid])
    frame["time"] = pd.to_datetime(frame["time"], format="mixed", errors="coerce")
    frame["numeric_value"] = pd.to_numeric(frame["numeric_value"], errors="coerce").astype("float32")
    core = pa.Table.from_pandas(frame, schema=pa.schema([
        pa.field("subject_id", pa.int64(), nullable=False), pa.field("time", pa.timestamp("us")),
        pa.field("code", pa.string(), nullable=False), pa.field("numeric_value", pa.float32()), pa.field("text_value", pa.large_string()),
    ]), preserve_index=False)
    module.DataSchema.validate(core)
    if duplicate_count:
        issues.append(add_issue("DUPLICATE_EVENT", "warning", "cross-table event stream", f"识别并去除 {duplicate_count} 条完全重复的 MEDS 候选事件。"))
    return {"events": valid, "issues": issues, "validator": "MEDS DataSchema.validate",
            "audit": add_audit(state, "质量校验 Agent", f"核心 Schema 校验通过；去除 {duplicate_count} 条重复候选后保留 {len(valid)} 条事件，使用 MEDS DataSchema.validate。")}


def write_outputs(state: State) -> State:
    """汇总 Agent：写本地受限输出和可公开的汇总统计，不导出原始标识。"""
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "data").mkdir(exist_ok=True)
    (OUTPUT / "metadata").mkdir(exist_ok=True)
    frame = pd.DataFrame(state["events"])
    frame["time"] = pd.to_datetime(frame["time"], format="mixed", errors="coerce")
    frame = frame.sort_values(["subject_id", "time"], na_position="first", kind="stable")
    event_table = pa.Table.from_pandas(frame, schema=pa.schema([
        pa.field("subject_id", pa.int64(), nullable=False), pa.field("time", pa.timestamp("us")), pa.field("code", pa.string(), nullable=False),
        pa.field("numeric_value", pa.float32()), pa.field("text_value", pa.string()), pa.field("source_table", pa.string()), pa.field("mapping_version", pa.string()),
    ]), preserve_index=False)
    pq.write_table(event_table, OUTPUT / "data" / "events.parquet")
    codes = sorted(frame.code.unique())
    pq.write_table(pa.table({"code": codes, "description": codes, "parent_codes": [[] for _ in codes]}), OUTPUT / "metadata" / "codes.parquet")
    metadata = {"dataset_name": "MIMIC-IV v3.1 local bounded ICU cohort", "dataset_version": "3.1", "etl_name": "LangGraph MEDS governance workflow", "etl_version": "1.0", "meds_version": "0.4", "created_at": datetime.now(timezone.utc).isoformat(), "license": "PhysioNet Credentialed Health Data License v1.5.0", "description": "Local only. Do not distribute derived row-level data.", "other_extension_columns": ["source_table", "mapping_version"]}
    (OUTPUT / "metadata" / "dataset.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    final_audit = add_audit(state, "结果汇总 Agent", f"在本地受限目录写入 {len(frame)} 条 MEDS 事件与汇总统计；未输出原始 subject_id / hadm_id。")
    summary = {"source_dataset": "MIMIC-IV v3.1", "cohort_selection": f"{LAB_SCAN_ROWS:,} 条检验记录前缀中与 ICU 表关联的前 {COHORT_SIZE} 名患者", "input_tables": 6, "sample_subjects": int(frame.subject_id.nunique()), "output_events": len(frame), "unique_codes": len(codes), "quality_issues": len(state["issues"]), "errors": sum(x["severity"] == "error" for x in state["issues"]), "warnings": sum(x["severity"] == "warning" for x in state["issues"]), "validator": state["validator"], "row_level_output_local_only": True}
    (OUTPUT / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(state["issues"]).to_csv(OUTPUT / "quality_issues.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(final_audit).to_csv(OUTPUT / "audit_log.csv", index=False, encoding="utf-8-sig")
    return {"output_files": [str(OUTPUT / "quality_summary.json"), str(OUTPUT / "quality_issues.csv"), str(OUTPUT / "audit_log.csv")], "audit": final_audit}


NODES = [("read_data", read_data), ("extract_objects", extract_objects), ("map_to_meds", map_to_meds), ("validate_events", validate_events), ("write_outputs", write_outputs)]


def main() -> None:
    graph = StateGraph(State)
    for name, node in NODES:
        graph.add_node(name, node)
    graph.add_edge(START, NODES[0][0])
    for (current, _), (following, _) in zip(NODES, NODES[1:]):
        graph.add_edge(current, following)
    graph.add_edge(NODES[-1][0], END)
    final = graph.compile().invoke({})
    print("Engine: LangGraph StateGraph")
    print(json.dumps(json.loads((OUTPUT / "quality_summary.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    print("Local-only summary files:")
    print("\n".join(final["output_files"]))


if __name__ == "__main__":
    main()
