"""LLM 响应缓存：按 prompt 内容哈希落盘，重跑不重复付费。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        self._path(key).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
