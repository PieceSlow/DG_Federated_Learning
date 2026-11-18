"""分级级别读取：``levels.yaml`` 是唯一来源，classify 与 labels 共用。

为什么单独抽出来：患者 360 的级别原先分别写在 ``classify._result`` 和
``labels/engine._dataset_frame`` 两处，改级别要改两个文件，容易再次不一致。
现在两边都调 :func:`level_of`，改 YAML 一处即可。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

LEVELS_PATH = Path(__file__).with_name("levels.yaml")

#: 文件缺失时的兜底值（与 levels.yaml 初始值一致）
DEFAULTS = {
    "C01": "L3",
    "C02": "L3",
    "C03": "L2",
    "C04": "L2",
    "C05": "L2",
    "C06": "L3",
    "C07": "L1",
    "C08": "L3",
}


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    if not LEVELS_PATH.exists():
        return dict(DEFAULTS)
    data = yaml.safe_load(LEVELS_PATH.read_text(encoding="utf-8")) or {}
    categories = data.get("categories") or {}
    return {str(key): str(value) for key, value in categories.items()}


def level_of(category_id: str, default: str | None = None) -> str:
    """按类别 ID 取级别；未知类别返回 ``default`` 或 ``DEFAULTS`` 兜底。"""
    levels = _load()
    return levels.get(category_id) or default or DEFAULTS.get(category_id, "L2")
