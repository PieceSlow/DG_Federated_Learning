"""Prompt 管理：按 id 加载 ``governance/prompts/<id>.yaml``，带版本与输出 schema。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

REQUIRED_FIELDS = ("id", "version", "system", "output_schema")


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    system: str
    output_schema: dict[str, Any]


@lru_cache(maxsize=None)
def load_prompt(prompt_id: str) -> Prompt:
    path = PROMPT_DIR / f"{prompt_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"未找到 prompt: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"prompt {path.name} 缺少字段: {', '.join(missing)}")
    return Prompt(
        id=str(data["id"]),
        version=str(data["version"]),
        system=str(data["system"]),
        output_schema=data["output_schema"],
    )
