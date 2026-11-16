"""候选生成与选择：粗筛相似度矩阵 → 每个目标字段选最优源字段。"""

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
    "method",
    "shared_source",
]


def build_candidates(
    sources: list[FieldView],
    targets: list[FieldView],
    weights: Weights | None = None,
) -> pd.DataFrame:
    weights = weights or Weights()
    records: list[dict] = []
    for target in targets:
        for source in sources:
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
    weights: Weights | None = None,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=SELECT_COLUMNS)
    best = candidates[candidates["rank"] == 1].copy()
    best = best[best["score"] >= threshold]
    counts = best.groupby(["source_table", "source_field"]).size()
    best["shared_source"] = [
        bool(counts.loc[(row.source_table, row.source_field)] > 1)
        for row in best.itertuples()
    ]
    best["method"] = "matcher"
    return best[SELECT_COLUMNS].reset_index(drop=True)
