"""阶段 07 · 概念归一（UMLS）。

把 ICD / 药品 / 检验编码映射到 UMLS 概念（CUI），并给出语义类型与置信度。

* 只在本地读取 UMLS 缓存，不联网、不发送数据；
* 映射不到就记 ``CONCEPT_UNMAPPED``，不猜；
* 模糊匹配带 ``confidence``，便于人工复核（半自动）。

缓存不存在时本阶段**跳过而不失败**，保证没有 UMLS 时流水线仍可运行。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from governance.concept_key import ConceptResolver
from governance.context import GovernanceContext, require_state
from governance.umls.config import UMLS_VERSION, cache_root
from governance.umls.link import ConceptLinker

COLUMNS = [
    "source_system",
    "source_code",
    "source_label",
    "cui",
    "concept_name",
    "sty",
    "confidence",
    "method",
]


def _empty_coverage() -> dict:
    return {"total": 0, "mapped": 0, "exact": 0, "fuzzy": 0}


def run(ctx: GovernanceContext) -> None:
    require_state(ctx, "tables")
    cache = cache_root(ctx.settings.output)
    if not (cache / "concepts.parquet").exists():
        ctx.log(
            "概念归一",
            f"未找到 UMLS 缓存（{cache}），跳过本阶段；"
            "需要时运行 python -m governance.umls.preprocess 后重跑。",
        )
        ctx.stats["concept_mapping"] = {"status": "skipped"}
        ctx.concept_map = None
        return

    linker = ConceptLinker(cache)
    rows: list[dict] = []
    coverage: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()

    def record(system: str, code: str, label: str | None,
               match, source_table: str) -> None:
        key = (system, str(code))
        if key in seen:
            return
        seen.add(key)
        bucket = coverage.setdefault(system, _empty_coverage())
        bucket["total"] += 1
        if match is None:
            ctx.add_issue(
                "CONCEPT_UNMAPPED", "warning", source_table,
                f"{system} 编码未映射到 UMLS 概念: {code}", record=f"{system}:{code}",
            )
            return
        bucket["mapped"] += 1
        bucket["exact" if match.method.endswith("exact") else "fuzzy"] += 1
        rows.append(
            {
                "source_system": system,
                "source_code": str(code),
                "source_label": label or "",
                "cui": match.cui,
                "concept_name": match.name,
                "sty": match.sty,
                "confidence": match.confidence,
                "method": match.method,
            }
        )

    tables = ctx.tables
    diagnoses = tables.get("diagnoses_icd")
    if diagnoses is not None:
        for _, row in diagnoses.drop_duplicates(["icd_version", "icd_code"]).iterrows():
            version = int(row["icd_version"])
            record(f"ICD{version}", str(row["icd_code"]), None,
                   linker.map_icd(version, row["icd_code"]), "diagnoses_icd")

    procedures = tables.get("procedures_icd")
    if procedures is not None:
        for _, row in procedures.drop_duplicates(["icd_version", "icd_code"]).iterrows():
            version = int(row["icd_version"])
            system = "ICD10PCS" if version == 10 else "ICD9CM_PROC"
            record(system, str(row["icd_code"]), None,
                   linker.map_procedure(version, row["icd_code"]), "procedures_icd")

    prescriptions = tables.get("prescriptions")
    if prescriptions is not None and "drug" in prescriptions.columns:
        for name in prescriptions["drug"].dropna().astype(str).unique():
            record("RXNORM", name, name, linker.map_drug(name), "prescriptions")

    lab_items = tables.get("d_labitems")
    lab_events = tables.get("labevents")
    if lab_items is not None and "label" in lab_items.columns:
        subset = lab_items.drop_duplicates("itemid")[["itemid", "label"]]
        if lab_events is not None and "itemid" in lab_events.columns:
            used = set(pd.to_numeric(lab_events["itemid"], errors="coerce").dropna().astype("int64"))
            subset = subset[subset["itemid"].isin(used)]
        for _, row in subset.iterrows():
            label = str(row["label"])
            record("LNC", label, label, linker.map_lab(label), "d_labitems")

    concept_map = pd.DataFrame(rows, columns=COLUMNS)
    if not concept_map.empty:
        concept_map = concept_map.sort_values(["source_system", "source_code"]).reset_index(drop=True)

    metadata = ctx.settings.governance / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    concept_map.to_parquet(metadata / "concept_map.parquet", index=False)
    ctx.concept_map = concept_map

    for system, bucket in coverage.items():
        bucket["coverage"] = (
            round(bucket["mapped"] / bucket["total"], 4) if bucket["total"] else None
        )
    totals = {
        key: sum(bucket[key] for bucket in coverage.values())
        for key in ("total", "mapped", "exact", "fuzzy")
    }
    totals["coverage"] = round(totals["mapped"] / totals["total"], 4) if totals["total"] else None
    mapped_concepts = (
        concept_map[["cui", "sty"]].drop_duplicates("cui") if not concept_map.empty
        else pd.DataFrame(columns=["cui", "sty"])
    )
    missing_sty = mapped_concepts[
        mapped_concepts["sty"].fillna("").astype(str).str.strip().eq("")
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "umls_version": UMLS_VERSION,
        "cache": str(cache),
        "sources": coverage,
        "overall": totals,
        "mapped_concepts": int(len(mapped_concepts)),
        "missing_sty": {
            "count": int(len(missing_sty)),
            "examples": missing_sty["cui"].head(10).tolist(),
        },
    }
    # ---- 未映射靶子清单：按事件频次排序，先知道"改哪几个最值" ----
    # 光有覆盖率不够：要知道"这 713 个编码里，哪 20 个覆盖了大部分事件"，
    # 优化才不是盲调。清单只做统计，不改任何映射结果。
    unmapped_top = pd.DataFrame(
        columns=["event_type", "source_system", "code", "label", "events", "share"]
    )
    events = getattr(ctx, "clinical_events", None)
    if events is not None and not events.empty and "code" in events.columns:
        resolver = ConceptResolver(ctx.concept_map)
        frame = events[["event_type", "code_system", "code", "display"]].copy()
        frame = frame.dropna(subset=["code_system", "code"])
        frame = frame[frame["code"].astype(str).str.strip() != ""]
        frame["mapped"] = [
            resolver.resolve(row.event_type, row.code_system, row.code, getattr(row, "display", None))
            is not None
            for row in frame.itertuples(index=False)
        ]
        total_events = int(len(frame))
        unmapped = frame[~frame["mapped"]]
        report["events"] = total_events
        report["unmapped_events"] = int(len(unmapped))
        report["unmapped_share"] = (
            round(len(unmapped) / total_events, 4) if total_events else 0.0
        )
        if not unmapped.empty:
            grouped = (
                unmapped.fillna({"display": ""})
                .groupby(["event_type", "code_system", "code", "display"], dropna=False)
                .size()
                .reset_index(name="events")
                .rename(columns={"display": "label"})
                .sort_values(["events", "event_type"], ascending=[False, True])
            )
            grouped["share"] = (grouped["events"] / total_events).round(4)
            unmapped_top = grouped.head(200).reset_index(drop=True)
        report["unmapped_top"] = unmapped_top.head(20).to_dict("records")

    (ctx.settings.governance / "quality").mkdir(parents=True, exist_ok=True)
    (ctx.settings.governance / "quality" / "concept_coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    unmapped_top.to_csv(
        ctx.settings.governance / "quality" / "concept_unmapped_top.csv",
        index=False,
        encoding="utf-8-sig",
    )

    ctx.stats["concept_mapping"] = report
    ctx.log(
        "概念归一",
        f"映射 {totals['mapped']}/{totals['total']} 个编码/术语到 UMLS 概念"
        f"（覆盖率 {totals['coverage']:.1%}，精确 {totals['exact']}、模糊 {totals['fuzzy']}），"
        f"产出 {len(concept_map)} 条 concept_map；"
        f"未映射事件 {report.get('unmapped_events', 0)}/{report.get('events', 0)}"
        f"（{report.get('unmapped_share', 0):.1%}，清单见 quality/concept_unmapped_top.csv）。",
    )
