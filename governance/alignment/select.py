"""候选生成与选择：相似度矩阵 → 每个「目标字段 × 源表」保留 top-k 候选。

与旧实现的区别：

* 不再用 33 条静态映射当 gold，也不只保留 rank=1；允许 1:N
  （同一目标字段由多个源字段提供，如 ``code`` 来自诊断/操作/处方），
  由约束层决定哪些进入最终映射；
* **选择粒度是「目标字段 × 源表」而不是「目标字段」**：``subject_id`` 在 8 张源表里
  都有，若只在全局取 top-3，``diagnoses_icd.subject_id`` 会被 ``patients.subject_id``
  挤出候选，下游按事件类型生成 SQL 时就拿不到字段。按源表各取最优，才是一张
  真正可用的字段映射表（"哪个源表的哪个字段供给这个目标字段"）。
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
    "table_rank",
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
    "table_rank",
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
    frame["table_rank"] = (
        frame.groupby(["target_table", "target_field", "source_table"])["score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    return frame.sort_values(
        ["target_table", "target_field", "source_table", "table_rank"]
    ).reset_index(drop=True)


def select_mappings(
    candidates: pd.DataFrame,
    threshold: float = 0.5,
    top_k: int = 3,
    declared: dict[tuple[str, str], set[str]] | None = None,
) -> pd.DataFrame:
    """每个「目标字段 × 源表」保留前 top_k 个候选；同一源字段可服务多个目标。

    ``declared`` 是目标 schema 里声明的可接受源字段（``target_schema.yaml`` 的
    ``source_fields``）。**声明过的字段是对齐契约的一部分**：匹配器只负责打分与
    排序，不因为字面不相似就把契约字段否掉（例如 ``formulary_drug_cd → code``、
    ``dose_val_rx → numeric_value``）。未声明的候选仍按阈值与类型族过滤。
    """
    if candidates.empty:
        return pd.DataFrame(columns=SELECT_COLUMNS)
    if "table_rank" not in candidates:
        candidates = candidates.assign(
            table_rank=candidates.groupby(
                ["target_table", "target_field", "source_table"]
            )["score"].rank(ascending=False, method="first").astype(int)
        )
    best = candidates[candidates["table_rank"] <= top_k].copy()
    declared_map = declared or {}
    best["declared"] = [
        str(row.source_field) in declared_map.get((row.target_table, row.target_field), ())
        for row in best.itertuples()
    ]
    type_ok = best["type_score"] >= 0.5
    score_ok = best["score"] >= threshold
    best = best[best["declared"] | (score_ok & type_ok)]
    if best.empty:
        return pd.DataFrame(columns=SELECT_COLUMNS)
    counts = best.groupby(["source_table", "source_field"]).size()
    best["shared_source"] = [
        bool(counts.loc[(row.source_table, row.source_field)] > 1)
        for row in best.itertuples()
    ]
    best["method"] = [
        "matcher" if (score >= threshold and type_score >= 0.5) else "declared"
        for score, type_score in zip(best["score"], best["type_score"])
    ]
    return best[SELECT_COLUMNS].reset_index(drop=True)
