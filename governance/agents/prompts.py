"""Prompt 管理：按 id 加载 ``governance/prompts/<id>.yaml``，带版本与输出 schema。

**为什么不用 ``yaml.safe_load`` 直接读**：YAML 1.1 把 ``on`` / ``off`` / ``yes`` / ``no``
当成布尔值。关系抽取的 schema 里恰好有一个字段叫 ``on``（连接列），
``safe_load`` 会把它变成 ``True`` 键，随后 ``json.dumps`` 又把它写成 ``"true"``——
模型照着这个被污染的 schema 输出 ``"true": "subject_id"``，校验器便找不到 ``on``。
实测 qwen3 就是这样连续两轮被驳回的。这里换一个不解析布尔值的 Loader，
让 schema 里的键名保持原样。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

REQUIRED_FIELDS = ("id", "version", "system", "output_schema")


class _StringKeyLoader(yaml.SafeLoader):
    """不把 on/off/yes/no 解析成布尔值的 SafeLoader。"""


# 只剔除 bool 隐式解析规则，其它类型（数字、列表、映射）保持原样
_StringKeyLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


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
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StringKeyLoader)
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"prompt {path.name} 缺少字段: {', '.join(missing)}")
    return Prompt(
        id=str(data["id"]),
        version=str(data["version"]),
        system=str(data["system"]),
        output_schema=data["output_schema"],
    )
