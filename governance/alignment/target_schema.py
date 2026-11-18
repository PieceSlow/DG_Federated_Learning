"""对象字段对齐的目标 schema：从 YAML 加载目标字段与允许的源表。

与 ``meds_targets.yaml`` 的区别：
* 本文件面向 ``clinical_events`` 的**对象字段**（课程任务一）；
* ``meds_targets.yaml`` 面向 MEDS 5 个核心字段（对外接口）。

只有源字段直接提供的目标字段进入匹配；派生字段由代码生成，见
``derived_fields.yaml``，不参与匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from governance.alignment.schema import FieldView

TARGET_SCHEMA_PATH = Path(__file__).with_name("target_schema.yaml")


@dataclass(frozen=True)
class ObjectTarget:
    table: str
    field: str
    dtype: str
    required: bool
    description: str
    source_tables: tuple[str, ...]
    source_fields: tuple[str, ...] = ()


def load_object_targets(path: Path | None = None) -> list[ObjectTarget]:
    source = Path(path) if path else TARGET_SCHEMA_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    targets: list[ObjectTarget] = []
    for table, fields in raw["targets"].items():
        for name, spec in fields.items():
            targets.append(
                ObjectTarget(
                    table=str(table),
                    field=str(name),
                    dtype=str(spec.get("dtype", "object")),
                    required=bool(spec.get("required", False)),
                    description=str(spec.get("description", "")),
                    source_tables=tuple(spec.get("source_tables", [])),
                    source_fields=tuple(spec.get("source_fields", [])),
                )
            )
    return targets


def target_views(targets: list[ObjectTarget]) -> list[FieldView]:
    return [
        FieldView(
            table=target.table,
            name=target.field,
            dtype=target.dtype,
            position=position,
        )
        for position, target in enumerate(targets)
    ]


def allowed_table_map(targets: list[ObjectTarget]) -> dict[tuple[str, str], set[str]]:
    return {
        (target.table, target.field): set(target.source_tables)
        for target in targets
        if target.source_tables
    }


def allowed_field_map(targets: list[ObjectTarget]) -> dict[tuple[str, str], set[str]]:
    return {
        (target.table, target.field): set(target.source_fields)
        for target in targets
        if target.source_fields
    }
