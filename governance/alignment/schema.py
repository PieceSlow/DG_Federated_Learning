"""字段视图：源字段（本地统计）与目标字段（数据集设计）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FieldView:
    table: str
    name: str
    dtype: str
    null_rate: float = 0.0
    cardinality: int = 0
    position: int = 0


def _family(dtype: str) -> str:
    text = str(dtype).lower()
    if any(token in text for token in ("datetime", "timestamp", "date")):
        return "time"
    if any(token in text for token in ("float", "double", "decimal")):
        return "float"
    if any(token in text for token in ("int", "bool")):
        return "int"
    return "text"


def family_of(dtype: str) -> str:
    return _family(dtype)


def extract_source_fields(tables: dict[str, pd.DataFrame], sample: int = 50_000) -> list[FieldView]:
    """抽取源字段特征；只保留聚合统计，不读取值内容。"""
    fields: list[FieldView] = []
    for table_name, frame in tables.items():
        if frame is None or frame.empty:
            continue
        view = frame.head(sample)
        for position, column in enumerate(view.columns):
            series = view[column]
            fields.append(
                FieldView(
                    table=str(table_name),
                    name=str(column),
                    dtype=str(series.dtype),
                    null_rate=round(float(series.isna().mean()), 4),
                    cardinality=int(series.nunique(dropna=True)),
                    position=position,
                )
            )
    return fields


def targets_from_gold(field_map: pd.DataFrame) -> list[FieldView]:
    """目标字段来自数据集设计（gold 映射的 target 侧）。"""
    targets: list[FieldView] = []
    for position, row in enumerate(field_map.itertuples()):
        targets.append(
            FieldView(
                table=str(row.dataset_table),
                name=str(row.dataset_field),
                dtype=str(row.dtype),
                position=position,
            )
        )
    return targets
