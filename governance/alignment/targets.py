"""MEDS 目标接口：从配置加载、校验，不包含自造聚合字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CORE_FIELDS = ("subject_id", "time", "code", "numeric_value", "text_value")


@dataclass(frozen=True)
class TargetField:
    name: str
    required: bool = False
    template: str | None = None
    source_hint: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class EventTarget:
    event_type: str
    source_tables: tuple[str, ...]
    fields: dict[str, TargetField]
    joins: dict[str, dict] = field(default_factory=dict)

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.fields.items() if spec.required)


def load_targets(path: Path | None = None) -> dict[str, EventTarget]:
    path = path or Path(__file__).with_name("meds_targets.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw.get("interface") != "MEDS":
        raise ValueError("目标接口必须是 MEDS")
    targets: dict[str, EventTarget] = {}
    for event_type, body in raw["event_types"].items():
        fields = {}
        for name, spec in body["fields"].items():
            if name not in CORE_FIELDS:
                raise ValueError(f"{event_type}: 非 MEDS 核心字段 {name}")
            fields[name] = TargetField(
                name=name,
                required=bool(spec.get("required", False)),
                template=spec.get("template"),
                source_hint=spec.get("source_hint"),
                note=spec.get("note"),
            )
        missing = [name for name in CORE_FIELDS if name not in fields]
        if missing:
            raise ValueError(f"{event_type}: 缺少 MEDS 字段 {missing}")
        for name in ("subject_id", "code"):
            if not fields[name].required:
                raise ValueError(f"{event_type}: {name} 必须 required")
        targets[event_type] = EventTarget(
            event_type=event_type,
            source_tables=tuple(body.get("source_tables", [])),
            fields=fields,
            joins=body.get("joins", {}),
        )
    return targets


def assert_no_derived_targets(targets: dict[str, EventTarget]) -> None:
    """接口里不允许出现自造聚合/派生字段。"""
    for target in targets.values():
        for name in target.fields:
            if name.startswith("n_") or name in {"total_los_days", "text_chars", "readmission_30d"}:
                raise ValueError(f"目标接口包含自造字段: {target.event_type}.{name}")
