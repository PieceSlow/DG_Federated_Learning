"""对齐覆盖率评估：不再以 33 条静态映射为 gold。

评价的是"目标字段是否被匹配到、必填字段是否覆盖、多少条需要人工复核"，
而不是"与某张人工表的精确一致率"。
"""

from __future__ import annotations

import pandas as pd

from governance.alignment.target_schema import ObjectTarget


def coverage(field_map: pd.DataFrame, targets: list[ObjectTarget]) -> dict:
    if field_map is None or field_map.empty:
        mapped: set[tuple[str, str]] = set()
    else:
        mapped = set(zip(field_map["target_table"], field_map["target_field"]))

    required = {(target.table, target.field) for target in targets if target.required}
    all_targets = {(target.table, target.field) for target in targets}
    required_mapped = required & mapped
    missing_required = sorted(f"{table}.{field}" for table, field in required - mapped)

    by_target: dict[str, dict] = {}
    for target in targets:
        key = (target.table, target.field)
        subset = (
            field_map[
                (field_map["target_table"] == target.table)
                & (field_map["target_field"] == target.field)
            ]
            if field_map is not None and not field_map.empty
            else pd.DataFrame()
        )
        by_target[f"{target.table}.{target.field}"] = {
            "required": target.required,
            "mapped": key in mapped,
            "candidates": int(len(subset)),
            "best_score": round(float(subset["score"].max()), 4) if not subset.empty else 0.0,
        }

    auto = int((field_map["status"] == "auto").sum()) if "status" in field_map else 0
    review = int((field_map["status"] == "review").sum()) if "status" in field_map else 0
    return {
        "targets_total": len(all_targets),
        "targets_mapped": len(mapped),
        "coverage": round(len(mapped) / len(all_targets), 4) if all_targets else 0.0,
        "required_total": len(required),
        "required_mapped": len(required_mapped),
        "required_coverage": round(len(required_mapped) / len(required), 4) if required else 1.0,
        "missing_required": missing_required,
        "auto_mappings": auto,
        "review_mappings": review,
        "by_target": by_target,
    }
