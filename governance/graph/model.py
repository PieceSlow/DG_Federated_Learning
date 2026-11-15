"""对象模型：概念 / 逻辑 / 物理三层定义的加载与校验。

``objects.yaml`` 是单一事实来源：对象、主键、属性、敏感级别、来源表与接口、关系都在这里。
代码只按模型执行，避免"对象定义"散落在多个模块里。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

MODEL_PATH = Path(__file__).resolve().parent / "objects.yaml"


@dataclass(frozen=True)
class RelationSpec:
    name: str
    from_object: str
    to_object: str
    on: str
    cardinality: str = "1:N"


@dataclass(frozen=True)
class ObjectSpec:
    name: str
    keys: tuple[str, ...]
    attributes: tuple[str, ...]
    sensitivity: str
    source_table: str
    interface: str
    concept: str


@dataclass(frozen=True)
class ObjectModel:
    version: str
    objects: dict[str, ObjectSpec]
    relations: tuple[RelationSpec, ...]

    def relation_names(self) -> tuple[str, ...]:
        return tuple(relation.name for relation in self.relations)


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ValueError(f"{where} 缺少字段: {key}")
    return mapping[key]


@lru_cache(maxsize=None)
def load_model(path: str | None = None) -> ObjectModel:
    model_path = Path(path) if path else MODEL_PATH
    data = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"对象模型不是映射: {model_path}")

    concept = _require(data, "concept", model_path.name)
    logical = _require(data, "logical", model_path.name)
    physical = _require(data, "physical", model_path.name)
    relations = data.get("relations", [])

    names = set(concept)
    if set(logical) != names or set(physical) != names:
        raise ValueError("概念/逻辑/物理三层的对象集合必须一致")

    objects: dict[str, ObjectSpec] = {}
    for name in names:
        keys = tuple(_require(logical[name], "keys", f"logical.{name}"))
        if not keys:
            raise ValueError(f"logical.{name} 的 keys 不能为空")
        objects[name] = ObjectSpec(
            name=name,
            keys=keys,
            attributes=tuple(logical[name].get("attributes", [])),
            sensitivity=str(_require(logical[name], "sensitivity", f"logical.{name}")),
            source_table=str(_require(physical[name], "source_table", f"physical.{name}")),
            interface=str(_require(physical[name], "interface", f"physical.{name}")),
            concept=str(concept[name]),
        )

    specs: list[RelationSpec] = []
    for item in relations:
        relation = RelationSpec(
            name=str(_require(item, "name", "relations")),
            from_object=str(_require(item, "from", "relations")),
            to_object=str(_require(item, "to", "relations")),
            on=str(_require(item, "on", "relations")),
            cardinality=str(item.get("cardinality", "1:N")),
        )
        if relation.from_object not in objects or relation.to_object not in objects:
            raise ValueError(f"关系 {relation.name} 引用了未定义的对象")
        if relation.on not in objects[relation.from_object].keys:
            raise ValueError(f"关系 {relation.name} 的 on 必须是 {relation.from_object} 的主键")
        specs.append(relation)

    if len({relation.name for relation in specs}) != len(specs):
        raise ValueError("关系名重复")

    return ObjectModel(
        version=str(data.get("version", "1.0")),
        objects=objects,
        relations=tuple(specs),
    )
