"""LLM 调用审计：每次调用（含缓存命中）追加一行 JSONL。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class UsageLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        prompt_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        cached: bool,
    ) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "prompt_id": prompt_id,
            "model": model,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "cost_usd": round(float(cost_usd), 6),
            "cached": bool(cached),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
