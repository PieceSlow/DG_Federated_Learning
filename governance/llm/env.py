"""轻量 .env 读取：key 只从本地文件/环境变量进入，不写入仓库。"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """读取 .env；已存在的环境变量优先，不覆盖。"""
    path = path or Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return {}
    loaded = {}
    for key, value in parse_env_file(path).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
