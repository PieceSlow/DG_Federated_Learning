"""匹配器组合：加权求和，权重可配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Weights:
    string: float = 0.45
    type: float = 0.25
    structure: float = 0.15
    value: float = 0.15

    def total(self) -> float:
        return self.string + self.type + self.structure + self.value


def combine(scores: dict[str, float], weights: Weights) -> float:
    total = weights.total()
    if total <= 0:
        raise ValueError("权重之和必须大于 0")
    weighted = (
        scores.get("string", 0.0) * weights.string
        + scores.get("type", 0.0) * weights.type
        + scores.get("structure", 0.0) * weights.structure
        + scores.get("value", 0.0) * weights.value
    )
    return round(weighted / total, 4)
