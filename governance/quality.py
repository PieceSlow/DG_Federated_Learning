"""阶段 03 · 质量校验。

两层校验：

**规范层**：MEDS 核心列必须满足官方 schema（类型、可空性），失败即终止——宁可不出数据，
也不出不合规的数据。

**业务层**：编码格式、编码字典、对象关联、时间一致性、同源重复。这些规则产生的是
"带标记的数据"而不是"被悄悄修正的数据"：孤立 ``hadm_id`` 置空但事件保留，时间越界只记录
告警不改写时间，重复记录保留第一条并留痕。

校验明细写入 ``governance/quality/issues.csv``（含 ``subject_id``，仅限本地）。
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from governance.context import GovernanceContext
from governance.meds import CODE_RE, core_schema, load_schema


def _check_events(ctx: GovernanceContext) -> None:
    objects = ctx.objects
    admission_ids = objects["admission_ids"]
    stay_ids = objects["stay_ids"]
    windows = objects["windows"]

    candidate_total = len(ctx.events)
    bad_hadm = bad_stay = bad_code = 0
    outside_window = 0
    cleaned: list[dict] = []
    for item in ctx.events:
        if item["subject_id"] is None or not item["code"]:
            ctx.add_issue("MEDS_REQUIRED_FIELD", "error", item["source_table"],
                          "subject_id 或 code 为空，事件被阻断")
            continue
        if not CODE_RE.match(item["code"]):
            bad_code += 1
            ctx.add_issue("CODE_FORMAT", "error", item["source_table"],
                          f"code 不符合 namespace//value 格式: {item['code'][:80]}",
                          subject=item["subject_id"], record=item["source_record_id"])
            continue
        hadm = item.get("hadm_id")
        if hadm is not None and int(hadm) not in admission_ids:
            bad_hadm += 1
            item["hadm_id"] = None
            ctx.add_issue("OBJECT_ASSOCIATION", "error", item["source_table"],
                          "hadm_id 不在住院对象中，已置空并保留事件",
                          subject=item["subject_id"], record=item["source_record_id"])
        stay = item.get("stay_id")
        if stay is not None and int(stay) not in stay_ids:
            bad_stay += 1
            item["stay_id"] = None
            ctx.add_issue("OBJECT_ASSOCIATION", "error", item["source_table"],
                          "stay_id 不在 ICU 对象中，已置空并保留事件",
                          subject=item["subject_id"], record=item["source_record_id"])

        when = item.get("time")
        hadm = item.get("hadm_id")
        if when is not None and hadm is not None and pd.notna(when) and int(hadm) in windows:
            admit, disch = windows[int(hadm)]
            if pd.notna(admit) and when < admit - pd.Timedelta(days=1):
                outside_window += 1
                ctx.add_issue("TEMPORAL_INCONSISTENCY", "warning", item["source_table"],
                              "事件时间早于入院时间 1 天以上",
                              subject=item["subject_id"], record=item["source_record_id"])
            elif pd.notna(disch) and when > disch + pd.Timedelta(days=1):
                outside_window += 1
                ctx.add_issue("TEMPORAL_INCONSISTENCY", "warning", item["source_table"],
                              "事件时间晚于出院时间 1 天以上",
                              subject=item["subject_id"], record=item["source_record_id"])
        cleaned.append(item)

    seen: set[tuple] = set()
    deduped: list[dict] = []
    duplicates = 0
    for item in cleaned:
        key = (item["source_table"], item["source_record_id"], item["code"],
               item["time"], item["numeric_value"], item["text_value"])
        if key in seen:
            duplicates += 1
            ctx.add_issue("DUPLICATE_EVENT", "warning", item["source_table"],
                          "同源记录重复事件，保留第一条",
                          subject=item["subject_id"], record=item["source_record_id"])
            continue
        seen.add(key)
        deduped.append(item)

    ctx.events = deduped
    ctx.stats.update(
        {
            "candidate_events": candidate_total,
            "output_events": len(deduped),
            "duplicates_removed": duplicates,
            "dropped_bad_code": bad_code,
            "orphan_hadm": bad_hadm,
            "orphan_stay": bad_stay,
            "events_outside_encounter": outside_window,
            "subjects_with_events": len({e["subject_id"] for e in deduped}),
        }
    )


def _validate_core_schema(ctx: GovernanceContext) -> None:
    core = pa.Table.from_pandas(
        pd.DataFrame(
            [
                {
                    "subject_id": e["subject_id"],
                    "time": e["time"],
                    "code": e["code"],
                    "numeric_value": e["numeric_value"],
                    "text_value": e["text_value"],
                }
                for e in ctx.events
            ]
        ),
        schema=core_schema(),
        preserve_index=False,
    )
    load_schema().DataSchema.validate(core)
    ctx.validators.append("MEDS DataSchema.validate (in-memory core)")


def run(ctx: GovernanceContext) -> None:
    _check_events(ctx)
    _validate_core_schema(ctx)
    ctx.issues_frame().to_csv(
        ctx.settings.governance / "quality" / "issues.csv", index=False, encoding="utf-8-sig"
    )
    errors = sum(v for k, v in ctx.issue_counts.items() if k.endswith("|error"))
    warnings = sum(v for k, v in ctx.issue_counts.items() if k.endswith("|warning"))
    ctx.log(
        "质量校验",
        f"核心 Schema 校验通过；去重 {ctx.stats['duplicates_removed']} 条，"
        f"孤立 hadm {ctx.stats['orphan_hadm']} 条、孤立 stay {ctx.stats['orphan_stay']} 条，"
        f"时间越界 {ctx.stats['events_outside_encounter']} 条；"
        f"error {errors} / warning {warnings}，最终 {len(ctx.events)} 条事件。",
    )
