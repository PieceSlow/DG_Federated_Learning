"""ObjectFinderAgent：从表结构与统计中提出对象候选。"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED = ("name", "source_table", "keys", "attributes", "sensitivity", "reason")


class ObjectFinderAgent(Agent):
    name = "ObjectFinderAgent"
    role = "对象识别"
    goal = "从表结构与统计中提出可作为对象的主体（人/物/事件）"
    inputs = ("schema_view", "stats_view")
    outputs = ("objects", "risks")
    tools = ()
    prompt_id = "object_finder"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        missing = [key for key in self.inputs if key not in payload]
        if missing:
            raise ValueError(f"{self.name} 缺少输入: {', '.join(missing)}")

        parsed, response, _ = self.ask(payload)
        objects = parsed.get("objects") or []
        if not objects:
            raise ValueError("ObjectFinderAgent 没有提出任何对象")
        for item in objects:
            absent = [key for key in REQUIRED if key not in item]
            if absent:
                raise ValueError(f"对象提案缺少字段: {', '.join(absent)}")

        trace = self.emit(
            started=started,
            payload=payload,
            decision=f"提出 {len(objects)} 类对象候选",
            output=parsed,
            response=response,
        )
        return AgentResult(
            ok=True,
            summary=f"提出 {len(objects)} 类对象、{len(parsed.get('risks', []))} 项风险",
            artifacts={"objects": objects, "risks": parsed.get("risks", [])},
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )
