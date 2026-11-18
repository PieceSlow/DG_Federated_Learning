"""阶段 06 · 字段对齐（schema matching，不再使用 33 条静态映射）。

对齐流程：

1. 从源表抽取字段视图（类型/空值率/基数，不读取值内容）；
2. 从 ``target_schema.yaml`` 读取目标字段与允许的源表；
3. 字符串 / 类型 / 结构 / 值域四个匹配器出分，加权组合；
4. 每个目标字段保留 top-k 候选（允许 1:N），按类型与分数给出 auto/review；
5. 输出 ``field_level.csv``、``derived_fields.csv``、``field_level_meds.csv``，
   覆盖率与待复核项进 ``alignment_report.json``；
6. **用映射结果生成 ``build_clinical_events.sql``**，并在内存 SQLite 里对账行数与类型分布
   —— 这一步是"对齐结果的下游消费者"，否则字段映射只是一张没人用的表。

没有 gold：评价的是覆盖率与复核量，不是"与某张人工表的一致率"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from governance.alignment import (
    allowed_field_map,
    allowed_table_map,
    build_candidates,
    extract_source_fields,
    load_object_targets,
    select_mappings,
    target_views,
)
from governance.alignment.agentic import align_with_agents
from governance.alignment.crosswalk import build_meds_crosswalk, validate_crosswalk
from governance.alignment.evaluate import coverage
from governance.alignment.schema import family_of
from governance.alignment.sql import build_sql_with_usage, validate_sql
from governance.context import GovernanceContext, require_state
from governance.text_notes import note_text_frame

DERIVED_PATH = Path(__file__).resolve().parent / "alignment" / "derived_fields.yaml"

FIELD_COLUMNS = [
    "target_table",
    "target_field",
    "target_dtype",
    "required",
    "source_table",
    "source_field",
    "source_dtype",
    "score",
    "string_score",
    "type_score",
    "structure_score",
    "value_score",
    "rank",
    "table_rank",
    "method",
    "status",
    "evidence",
]

DERIVED_COLUMNS = ["dataset_table", "field", "kind", "inputs", "formula", "note"]


def _derived_fields() -> pd.DataFrame:
    raw = yaml.safe_load(DERIVED_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for dataset, fields in raw["datasets"].items():
        for item in fields:
            rows.append(
                {
                    "dataset_table": dataset,
                    "field": item["field"],
                    "kind": item["kind"],
                    "inputs": "、".join(item.get("inputs", [])),
                    "formula": item.get("formula", ""),
                    "note": item.get("note", ""),
                }
            )
    return pd.DataFrame(rows, columns=DERIVED_COLUMNS)


def _record_map(events: pd.DataFrame, p360: pd.DataFrame) -> pd.DataFrame:
    record_map = events[
        ["record_id", "subject_id", "hadm_id", "stay_id", "source_table",
         "source_record_id", "event_type", "split"]
    ].copy()
    record_map["dataset_table"] = "clinical_events"

    p360_map = p360[["subject_id", "split"]].copy()
    p360_map["record_id"] = "P360:" + p360_map["subject_id"].astype(str)
    p360_map["hadm_id"] = None
    p360_map["stay_id"] = None
    p360_map["source_table"] = "patients"
    p360_map["event_type"] = "PatientSummary"
    p360_map["dataset_table"] = "patient_360"
    p360_map["source_record_id"] = "derived:patient_360:" + p360_map["subject_id"].astype(str)
    return pd.concat([record_map, p360_map[record_map.columns]], ignore_index=True)


def _status(score: float, rank: int, source_dtype: str, target_dtype: str, required: bool) -> str:
    source_family, target_family = family_of(source_dtype), family_of(target_dtype)
    dtype_ok = source_family == target_family or {source_family, target_family} == {"int", "float"}
    if required and not dtype_ok:
        return "review"
    if rank == 1 and score >= 0.7 and dtype_ok:
        return "auto"
    return "review"


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")

    targets = load_object_targets()
    source_tables = dict(ctx.tables)
    source_tables["discharge"] = note_text_frame(ctx)
    sources = extract_source_fields(source_tables)

    candidates = build_candidates(
        sources,
        target_views(targets),
        allowed_tables=allowed_table_map(targets),
    )
    # 目标字段还可以限定允许的源字段（如 display 只接受 long_title/label/drug）
    allowed_fields = allowed_field_map(targets)
    if not candidates.empty and allowed_fields:
        mask = [
            (row.target_table, row.target_field) not in allowed_fields
            or row.source_field in allowed_fields[(row.target_table, row.target_field)]
            for row in candidates.itertuples()
        ]
        candidates = candidates[mask].reset_index(drop=True)
        candidates["rank"] = (
            candidates.groupby(["target_table", "target_field"])["score"]
            .rank(ascending=False, method="first")
            .astype(int)
        )
        candidates["table_rank"] = (
            candidates.groupby(["target_table", "target_field", "source_table"])["score"]
            .rank(ascending=False, method="first")
            .astype(int)
        )
    selected = select_mappings(
        candidates, threshold=0.5, top_k=3, declared=allowed_fields
    )

    spec = {(target.table, target.field): target for target in targets}
    rows: list[dict] = []
    if not selected.empty:
        detail = candidates.merge(
            selected[
                ["target_table", "target_field", "source_table", "source_field", "method"]
            ],
            on=["target_table", "target_field", "source_table", "source_field"],
            how="inner",
        )
        for row in detail.itertuples():
            target = spec[(row.target_table, row.target_field)]
            status = _status(
                float(row.score),
                int(row.table_rank),
                str(row.source_dtype),
                str(row.target_dtype),
                bool(target.required),
            )
            rows.append(
                {
                    "target_table": row.target_table,
                    "target_field": row.target_field,
                    "target_dtype": target.dtype,
                    "required": bool(target.required),
                    "source_table": row.source_table,
                    "source_field": row.source_field,
                    "source_dtype": row.source_dtype,
                    "score": float(row.score),
                    "string_score": float(row.string_score),
                    "type_score": float(row.type_score),
                    "structure_score": float(row.structure_score),
                    "value_score": float(row.value_score),
                    "rank": int(row.rank),
                    "table_rank": int(row.table_rank),
                    "method": str(row.method),
                    "status": status,
                    "evidence": (
                        f"string={row.string_score:.2f};type={row.type_score:.2f};"
                        f"structure={row.structure_score:.2f};value={row.value_score:.2f}"
                    ),
                }
            )
    field_map = pd.DataFrame(rows, columns=FIELD_COLUMNS)
    report = coverage(field_map, targets)

    agent_selected = None
    agent_info: dict = {"status": "skipped", "reason": "provider=off"}
    if ctx.llm is not None and ctx.llm.config.provider != "off":
        agent_selected, agent_info = align_with_agents(ctx, candidates)

    dest = ctx.settings.governance / "mapping"
    dest.mkdir(parents=True, exist_ok=True)
    field_map.to_csv(dest / "field_level.csv", index=False, encoding="utf-8-sig")
    derived = _derived_fields()
    derived.to_csv(dest / "derived_fields.csv", index=False, encoding="utf-8-sig")

    meds_crosswalk = build_meds_crosswalk()
    validate_crosswalk(meds_crosswalk)
    meds_crosswalk.to_csv(dest / "field_level_meds.csv", index=False, encoding="utf-8-sig")

    (dest / "MAPPING.md").write_text(
        "# 字段映射说明\n\n"
        "本目录的三套文件用途不同：\n\n"
        "* `field_level.csv`：**匹配结果**。由 name/type/structure/value 四个匹配器生成候选，"
        "top-k 选择后给出 auto/review；不再有静态 33 条表。\n"
        "* `derived_fields.csv`：**派生/聚合字段**（record_id、event_type、patient_360 的 n_* 等），"
        "不参与源字段匹配，单独说明公式与输入。\n"
        "* `field_level_meds.csv`：**MEDS 接口交叉表**，9 类事件 × 5 个核心字段，"
        "用于检查对外接口字段是否有源字段可落。\n\n"
        "`record_level.parquet` 是记录级血缘；"
        "`field_level_auto.csv` / `field_level_agent.csv` 是匹配器/智能体的候选与对照。\n",
        encoding="utf-8",
    )
    selected.to_csv(dest / "field_level_auto.csv", index=False, encoding="utf-8-sig")
    if agent_selected is not None and not agent_selected.empty:
        agent_selected.to_csv(dest / "field_level_agent.csv", index=False, encoding="utf-8-sig")
    candidates.to_parquet(dest / "match_candidates.parquet", index=False)

    (ctx.settings.governance / "quality").mkdir(parents=True, exist_ok=True)
    (ctx.settings.governance / "quality" / "alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if agent_selected is not None:
        (ctx.settings.governance / "quality" / "alignment_agent_report.json").write_text(
            json.dumps(agent_info, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        sql, sql_usage = build_sql_with_usage(field_map)
        (dest / "build_clinical_events.sql").write_text(sql, encoding="utf-8")
        expected = {
            str(key): int(value)
            for key, value in ctx.clinical_events["event_type"].value_counts().items()
        }
        sql_report = validate_sql(sql, ctx.tables, note_text_frame(ctx), expected)
        sql_report["mapping_usage"] = sql_usage
    except Exception as exc:  # noqa: BLE001 - 生成失败进报告，不中断流水线
        sql_report = {"status": "error", "error": str(exc)}
    (ctx.settings.governance / "quality" / "sql_execution_report.json").write_text(
        json.dumps(sql_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    typed = _record_map(ctx.clinical_events, ctx.patient_360)
    typed["hadm_id"] = pd.array(typed["hadm_id"], dtype="Int64")
    typed["stay_id"] = pd.array(typed["stay_id"], dtype="Int64")
    typed.to_parquet(dest / "record_level.parquet", index=False)

    ctx.field_map = field_map
    ctx.field_map_meds = meds_crosswalk
    ctx.stats.update(
        {
            "field_mappings": int(len(field_map)),
            "meds_field_mappings": int(len(meds_crosswalk)),
            "derived_fields": int(len(derived)),
            "record_mappings": int(len(typed)),
            "alignment": {
                "targets": report["targets_total"],
                "mapped": report["targets_mapped"],
                "coverage": report["coverage"],
                "required_coverage": report["required_coverage"],
                "missing_required": report["missing_required"],
                "auto": report["auto_mappings"],
                "review": report["review_mappings"],
                "agent": agent_info,
            },
            "sql_execution": sql_report,
        }
    )
    ctx.log(
        "字段对齐",
        f"schema matching：目标 {report['targets_total']} 个，覆盖 {report['targets_mapped']} 个"
        f"（{report['coverage']:.1%}），必填覆盖 {report['required_coverage']:.1%}；"
        f"auto {report['auto_mappings']} / review {report['review_mappings']}；"
        f"派生字段 {len(derived)} 条；MEDS 交叉表 {len(meds_crosswalk)} 条；"
        f"SQL 对账 {sql_report.get('status')}（{sql_report.get('rows', 0)} 行）。",
    )
