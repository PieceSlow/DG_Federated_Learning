"""候选生成与选择：相似度矩阵 → 每个目标字段保留 top-k 候选。

与旧实现的区别：不再用 33 条静态映射当 gold，也不只保留 rank=1；允许 1:N
（同一目标字段由多个源字段提供，如 ``code`` 来自诊断/操作/处方），由约束层
决定哪些进入最终映射。
"""

from __future__ import annotations

import pandas as pd

from governance.alignment.combine import Weights, combine
from governance.alignment.matchers import (
    string_score,
    structure_score,
    type_score,
    value_score,
)
from governance.alignment.schema import FieldView

CANDIDATE_COLUMNS = [
    "target_table",
    "target_field",
    "source_table",
    "source_field",
    "string_score",
    "type_score",
    "structure_score",
    "value_score",
    "score",
    "rank",
    "source_dtype",
    "target_dtype",
    "source_null_rate",
]

SELECT_COLUMNS = [
    "target_table",
    "target_field",
    "source_table",
    "source_field",
    "score",
    "rank",
    "method",
    "shared_source",
]


def build_candidates(
    sources: list[FieldView],
    targets: list[FieldView],
    weights: Weights | None = None,
    allowed_tables: dict[tuple[str, str], set[str]] | None = None,
) -> pd.DataFrame:
    weights = weights or Weights()
    allowed_tables = allowed_tables or {}
    records: list[dict] = []
    for target in targets:
        allowed = allowed_tables.get((target.table, target.name))
        for source in sources:
            if allowed is not None and source.table not in allowed:
                continue
            parts = {
                "string": string_score(source, target),
                "type": type_score(source, target),
                "structure": structure_score(source, target),
                "value": value_score(source, target),
            }
            records.append(
                {
                    "target_table": target.table,
                    "target_field": target.name,
                    "source_table": source.table,
                    "source_field": source.name,
                    "string_score": parts["string"],
                    "type_score": parts["type"],
                    "structure_score": parts["structure"],
                    "value_score": parts["value"],
                    "score": combine(parts, weights),
                    "source_dtype": source.dtype,
                    "target_dtype": target.dtype,
                    "source_null_rate": source.null_rate,
                }
            )
    frame = pd.DataFrame(records, columns=[c for c in CANDIDATE_COLUMNS if c != "rank"])
    if frame.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    frame["rank"] = (
        frame.groupby(["target_table", "target_field"])["score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    return frame.sort_values(
        ["target_table", "target_field", "rank"]
    ).reset_index(drop=True)


def select_mappings(
    candidates: pd.DataFrame,
    threshold: float = 0.5,
    top_k: int = 3,
) -> pd.DataFrame:
    """保留每个目标字段的前 top_k 个候选；允许同一源字段服务多个目标。"""
    if candidates.empty:
        return pd.DataFrame(columns=SELECT_COLUMNS)
    best = candidates[(candidates["rank"] <= top_k) & (candidates["score"] >= threshold)].copy()
    # 类型族必须兼容（int/float 视为兼容），避免把时间字段映射到编码字段
    best = best[best["type_score"] >= 0.5]
    if best.empty:
        return pd.DataFrame(columns=SELECT_COLUMNS)
    counts = best.groupby(["source_table", "source_field"]).size()
    best["shared_source"] = [
        bool(counts.loc[(row.source_table, row.source_field)] > 1)
        for row in best.itertuples()
    ]
    best["method"] = "matcher"
    return best[SELECT_COLUMNS].reset_index(drop=True)
