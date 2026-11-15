"""以人工确认的 33 条映射为 gold，评测匹配算法。"""

from __future__ import annotations

import pandas as pd

SKIP_SOURCES = {"derived", "多个源表", "-", ""}


def gold_pairs(field_map: pd.DataFrame, source_tables: set[str]) -> dict[tuple[str, str], tuple[str, str]]:
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for row in field_map.itertuples():
        source_table = str(row.source_table)
        source_field = str(row.source_field)
        if source_table not in source_tables or source_field in SKIP_SOURCES:
            continue
        pairs[(str(row.dataset_table), str(row.dataset_field))] = (source_table, source_field)
    return pairs


def evaluate(
    selected: pd.DataFrame,
    candidates: pd.DataFrame,
    field_map: pd.DataFrame,
) -> dict:
    source_tables = set(candidates["source_table"].unique()) if not candidates.empty else set()
    gold = gold_pairs(field_map, source_tables)

    predicted = {
        (row.target_table, row.target_field): (row.source_table, row.source_field)
        for row in selected.itertuples()
    }

    exact = sum(1 for key, value in predicted.items() if gold.get(key) == value)
    precision = round(exact / len(predicted), 4) if predicted else 0.0
    recall = round(exact / len(gold), 4) if gold else 0.0

    top3_hits = 0
    if not candidates.empty:
        top3 = candidates[candidates["rank"] <= 3]
        for key, expected in gold.items():
            subset = top3[(top3["target_table"] == key[0]) & (top3["target_field"] == key[1])]
            if any(
                (row.source_table, row.source_field) == expected
                for row in subset.itertuples()
            ):
                top3_hits += 1
    top3_recall = round(top3_hits / len(gold), 4) if gold else 0.0

    differences = []
    for key, expected in gold.items():
        actual = predicted.get(key)
        if actual != expected:
            differences.append(
                {
                    "target": f"{key[0]}.{key[1]}",
                    "gold": f"{expected[0]}.{expected[1]}",
                    "predicted": f"{actual[0]}.{actual[1]}" if actual else None,
                }
            )

    return {
        "gold_pairs": len(gold),
        "predicted_pairs": len(predicted),
        "exact_matches": exact,
        "precision": precision,
        "recall": recall,
        "top3_recall": top3_recall,
        "differences": differences,
    }
