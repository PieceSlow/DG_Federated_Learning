"""MEDS 目标接口：从 ``meds_targets.yaml`` 加载、校验，不包含自造聚合字段。

``meds_targets.yaml`` 是接口的唯一声明，被三处消费：

* :mod:`governance.standardize` 按它产出事件（表、模板、时间/数值/文本列）；
* :mod:`governance.alignment.crosswalk` / :mod:`governance.alignment.interface`
  用 ``uses`` 从 ``field_level.csv``（schema matching 结果）回填落地字段；
* :mod:`governance.alignment.interface` 用 ``code_namespaces`` / ``required``
  校验实际事件流。

因此这里的 dataclass 要保留"声明细节"（模板、源列、关联表），而不是只留 5 个字段名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CORE_FIELDS = ("subject_id", "time", "code", "numeric_value", "text_value")

#: 每个字段允许的来源类型
SOURCES = ("spec", "derived", "mapping", "none")

#: MEDS 字段 → ``clinical_events`` 的同义目标字段（对齐表用的是 clinical_events 口径）
FIELD_ALIASES = {
    "subject_id": "subject_id",
    "time": "event_time",
    "code": "code",
    "numeric_value": "numeric_value",
    "text_value": "text_value",
}


@dataclass(frozen=True)
class TargetField:
    name: str
    required: bool = False
    source: str = "none"
    template: str | None = None
    uses: tuple[str, ...] = ()
    column: str | None = None
    via: str | None = None
    fallback: str | None = None
    only_if_null: tuple[str, ...] = ()
    max_chars: int | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"字段来源必须是 {SOURCES}，得到 {self.source!r}")
        if self.source == "spec" and not self.template:
            raise ValueError("source=spec 必须给 template")
        if self.source == "derived" and not self.column:
            raise ValueError("source=derived 必须给 column")
        if self.source == "none" and self.required:
            raise ValueError("required 字段不能声明 source=none（必填列必须有来源）")

    @property
    def source_hint(self) -> str | None:
        """声明中吃到的第一个源列（``表.列``），供文档与旧调用方使用。"""
        if self.uses:
            return self.uses[0]
        if self.column:
            return f"{self.via}.{self.column}" if self.via else self.column
        return None


@dataclass(frozen=True)
class EventTarget:
    event_type: str
    source_tables: tuple[str, ...]
    fields: dict[str, TargetField]
    description: str = ""
    table: str = ""
    join: dict = field(default_factory=dict)
    emit_if: tuple[str, ...] = ()
    check_hadm: bool = False
    skip_issue: dict = field(default_factory=dict)
    dictionary: dict = field(default_factory=dict)
    code_namespaces: tuple[str, ...] = ()
    keys: dict = field(default_factory=dict)
    extractor: str = "structured"

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
                source=str(spec.get("source", "none")),
                template=spec.get("template"),
                uses=tuple(spec.get("uses", ())),
                column=spec.get("column"),
                via=spec.get("via"),
                fallback=spec.get("fallback"),
                only_if_null=tuple(spec.get("only_if_null", ())),
                max_chars=spec.get("max_chars"),
                note=spec.get("note"),
            )
        missing = [name for name in CORE_FIELDS if name not in fields]
        if missing:
            raise ValueError(f"{event_type}: 缺少 MEDS 字段 {missing}")
        for name in ("subject_id", "code"):
            if not fields[name].required:
                raise ValueError(f"{event_type}: {name} 必须 required")
        target = EventTarget(
            event_type=event_type,
            source_tables=tuple(body.get("source_tables", [])),
            fields=fields,
            description=str(body.get("description", "")),
            table=str(body.get("table", "")),
            join=dict(body.get("join") or {}),
            emit_if=tuple(body.get("emit_if", ())),
            check_hadm=bool(body.get("check_hadm", False)),
            skip_issue=dict(body.get("skip_issue") or {}),
            dictionary=dict(body.get("dictionary") or {}),
            code_namespaces=tuple(body.get("code_namespaces", (event_type,))),
            keys=dict(body.get("keys") or {}),
            extractor=str(body.get("extractor", "structured")),
        )
        targets[event_type] = target
    if not targets:
        raise ValueError("接口声明里没有任何事件类型")
    return targets


def source_gaps(path: Path | None = None) -> dict[str, str]:
    """接口声明之外、必须讲清楚的缺口（用于报告，不参与匹配）。"""
    path = path or Path(__file__).with_name("meds_targets.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(key): str(value).strip() for key, value in (raw.get("source_gaps") or {}).items()}


def declared_namespaces(targets: dict[str, EventTarget] | None = None) -> set[str]:
    """声明的编码命名空间（NOTE 事件会带出 4 个）。"""
    targets = targets or load_targets()
    return {name for target in targets.values() for name in target.code_namespaces}


def assert_no_derived_targets(targets: dict[str, EventTarget]) -> None:
    """接口里不允许出现自造聚合/派生字段。"""
    for target in targets.values():
        for name in target.fields:
            if name.startswith("n_") or name in {"total_los_days", "text_chars", "readmission_30d"}:
                raise ValueError(f"目标接口包含自造字段: {target.event_type}.{name}")
