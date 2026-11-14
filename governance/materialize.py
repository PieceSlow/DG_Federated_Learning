"""阶段 04 · 事件仓储落盘。

按 MEDS 目录规范写出事件流，并在**写盘后重新读回**再做一次校验：

* ``data/events.parquet``：核心 5 列 + 6 个扩展列（住院号、ICU 号、来源表、来源记录、
  映射版本、抽取器）；
* ``metadata/codes.parquet``：编码 → 描述 / 父编码；
* ``metadata/subject_splits.parquet``：患者级划分；
* ``metadata/dataset.json``：数据集元数据。

"写完再读回校验"是刻意的：内存里对、磁盘上错的情况（编码、类型、排序）只有读回来才暴露。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from governance.context import GovernanceContext
from governance.meds import (
    code_description,
    dataset_metadata,
    event_store_schema,
    events_to_frame,
    load_schema,
    subject_split,
)


def run(ctx: GovernanceContext) -> None:
    if not ctx.events:
        raise RuntimeError("没有可落盘的事件；请先完成标准化与质量校验")

    store: Path = ctx.settings.eventstore
    (store / "data").mkdir(parents=True, exist_ok=True)
    (store / "metadata").mkdir(parents=True, exist_ok=True)

    frame = events_to_frame(ctx.events)
    table = pa.Table.from_pandas(frame, schema=event_store_schema(), preserve_index=False)
    pq.write_table(table, store / "data" / "events.parquet")

    _write_codes(ctx, frame, store)

    subjects = sorted({int(x) for x in frame["subject_id"].unique()})
    split_of = {subject: subject_split(subject) for subject in subjects}
    pq.write_table(
        pa.table(
            {
                "subject_id": pa.array(subjects, pa.int64()),
                "split": pa.array([split_of[s] for s in subjects], pa.string()),
            }
        ),
        store / "metadata" / "subject_splits.parquet",
    )

    metadata = dataset_metadata()
    (store / "metadata" / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ctx.stats.update(
        {
            "eventstore_rows": int(len(frame)),
            "unique_codes": int(frame["code"].nunique()),
            "subject_splits": {
                key: sum(1 for value in split_of.values() if value == key)
                for key in ("train", "tuning", "held_out")
            },
            "events_by_source": {str(k): int(v) for k, v in frame["source_table"].value_counts().items()},
        }
    )

    validators = _validate_readback(ctx, store, metadata)
    ctx.validators.extend(validators)
    ctx.log(
        "事件仓储落盘",
        f"写入 {len(frame)} 条事件、{ctx.stats['unique_codes']} 个编码、{len(subjects)} 个患者划分；"
        f"写盘后重新读回并完成 {len(validators)} 项规范校验。",
    )


def _write_codes(ctx: GovernanceContext, frame: pd.DataFrame, store: Path) -> None:
    codes = sorted(frame["code"].unique())
    descriptions, parents = zip(*(code_description(code, ctx.tables) for code in codes))
    pq.write_table(
        pa.table(
            {
                "code": pa.array(codes, pa.string()),
                "description": pa.array(descriptions, pa.string()),
                "parent_codes": pa.array(list(parents), pa.list_(pa.string())),
            }
        ),
        store / "metadata" / "codes.parquet",
    )


def _validate_readback(ctx: GovernanceContext, store: Path, metadata: dict) -> list[str]:
    schema = load_schema()
    readback = pq.read_table(store / "data" / "events.parquet")
    schema.DataSchema.validate(
        readback.select(["subject_id", "time", "code", "numeric_value", "text_value"])
    )
    schema.CodeMetadataSchema.validate(pq.read_table(store / "metadata" / "codes.parquet"))
    schema.SubjectSplitSchema.validate(pq.read_table(store / "metadata" / "subject_splits.parquet"))
    schema.DatasetMetadataSchema.validate(metadata)
    if not readback.select(["subject_id"]).to_pandas()["subject_id"].is_monotonic_increasing:
        raise RuntimeError("events.parquet 未按 subject_id 有序")
    return [
        "MEDS DataSchema.validate (readback)",
        "MEDS CodeMetadataSchema.validate",
        "MEDS SubjectSplitSchema.validate",
        "MEDS DatasetMetadataSchema.validate",
        "subject_id ordering check",
    ]
