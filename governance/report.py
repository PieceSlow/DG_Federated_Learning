"""阶段 11 · 质量报告与资产摘要。

把整条链路的结果汇成两份可对外引用的统计（都是聚合指标，不含患者行）：

* ``governance/quality/event_stream.json``：事件流的规模、来源、划分、校验项与告警；
* ``governance/quality/governed_dataset.json``：治理后数据集的外键覆盖、孤儿、重复、空值率。

同时把关键数字打印到控制台，方便课堂演示时直接对着看。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from governance.context import GovernanceContext, require_state


def _event_stream(ctx: GovernanceContext) -> dict:
    events = ctx.clinical_events
    per_subject = events.groupby("subject_id").size()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cohort_size": int(ctx.stats.get("cohort_size", 0)),
        "event_store": {
            "rows": int(ctx.stats.get("eventstore_rows", 0)),
            "unique_codes": int(ctx.stats.get("unique_codes", 0)),
            "by_source": ctx.stats.get("events_by_source", {}),
            "subject_splits": ctx.stats.get("subject_splits", {}),
        },
        "pipeline": {
            "candidate_events": int(ctx.stats.get("candidate_events", 0)),
            "output_events": int(ctx.stats.get("output_events", 0)),
            "duplicates_removed": int(ctx.stats.get("duplicates_removed", 0)),
            "dropped_bad_code": int(ctx.stats.get("dropped_bad_code", 0)),
            "orphan_hadm": int(ctx.stats.get("orphan_hadm", 0)),
            "orphan_stay": int(ctx.stats.get("orphan_stay", 0)),
            "events_outside_encounter": int(ctx.stats.get("events_outside_encounter", 0)),
            "notes_used": int(ctx.stats.get("notes_used", 0)),
            "note_events": int(ctx.stats.get("note_events", 0)),
        },
        "issues": {
            "counts": dict(ctx.issue_counts),
            "errors": sum(v for k, v in ctx.issue_counts.items() if k.endswith("|error")),
            "warnings": sum(v for k, v in ctx.issue_counts.items() if k.endswith("|warning")),
        },
        "events_per_subject": {
            "min": int(per_subject.min()) if len(per_subject) else 0,
            "median": int(per_subject.median()) if len(per_subject) else 0,
            "max": int(per_subject.max()) if len(per_subject) else 0,
        },
        "validators": ctx.validators,
        "row_level_output_local_only": True,
    }


def _governed_dataset(ctx: GovernanceContext) -> dict:
    events = ctx.clinical_events
    p360 = ctx.patient_360
    hadm_set = set(ctx.tables["admissions"]["hadm_id"].astype("int64"))
    orphan = int((~events["hadm_id"].dropna().astype("int64").isin(hadm_set)).sum())
    duplicate = int(events["record_id"].duplicated().sum())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cohort_size": int(len(p360)),
        "clinical_events": {
            "rows": int(len(events)),
            "by_type": {k: int(v) for k, v in events["event_type"].value_counts().items()},
            "subjects": int(events["subject_id"].nunique()),
            "hadm_non_null": int(events["hadm_id"].notna().sum()),
            "hadm_null": int(events["hadm_id"].isna().sum()),
            "stay_linked": int(events["stay_id"].notna().sum()),
            "orphan_hadm": orphan,
            "duplicate_record_id": duplicate,
            "time_null": int(events["event_time"].isna().sum()),
            "note_text_chars": int(
                events.loc[events["event_type"] == "ClinicalNote", "text_value"].str.len().sum()
            ),
        },
        "patient_360": {
            "rows": int(len(p360)),
            "mortality_rate": round(float(p360["mortality"].mean()), 4),
            "median_los_days": round(float(p360["total_los_days"].median()), 2),
            "notes_coverage": round(float((p360["n_notes"] > 0).mean()), 4),
        },
        "split": {k: int(v) for k, v in p360["split"].value_counts().items()},
        "null_rates": {
            col: round(float(events[col].isna().mean()), 4)
            for col in ("hadm_id", "stay_id", "event_time", "numeric_value", "icd_chapter")
        },
    }


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "clinical_events", "patient_360")
    stream = _event_stream(ctx)
    governed = _governed_dataset(ctx)

    quality_dir = ctx.settings.governance / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / "event_stream.json").write_text(
        json.dumps(stream, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (quality_dir / "governed_dataset.json").write_text(
        json.dumps(governed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.stats["event_stream_report"] = stream
    ctx.stats["governed_dataset_report"] = governed

    issues = stream["issues"]
    print()
    print("治理结果摘要")
    print(f"  队列患者       : {stream['cohort_size']}")
    print(f"  MEDS 事件      : {stream['event_store']['rows']}（{stream['event_store']['unique_codes']} 个编码）")
    print(f"  统一事件表      : {governed['clinical_events']['rows']} 行")
    print(f"  患者 360       : {governed['patient_360']['rows']} 行")
    print(f"  告警 / 错误     : {issues['warnings']} / {issues['errors']}")
    print(f"  患者划分        : {governed['split']}")
    print(f"  ICU 挂接        : {governed['clinical_events']['stay_linked']} 条")
    print(f"  输出目录        : {ctx.settings.output}")
    ctx.log(
        "质量报告",
        f"事件流 {stream['event_store']['rows']} 条、统一事件表 {governed['clinical_events']['rows']} 行、"
        f"患者 360 {governed['patient_360']['rows']} 行；告警 {issues['warnings']}、错误 {issues['errors']}。",
    )
