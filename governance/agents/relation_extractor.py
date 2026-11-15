"""RelationExtractorAgent：为已确认的对象提出关系（边）。"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED = ("name", "from", "to", "on", "cardinality")


class RelationExtractorAgent(Agent):
    name = "RelationExtractorAgent"
    role = "关系抽取"
    goal = "为对象定义提出可执行的关系（边），并给出置信度"
    inputs = ("objects", "schema_view")
    outputs = ("relations",)
    tools = ()
    prompt_id = "relation_extractor"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        missing = [key for key in self.inputs if key not in payload]
        if missing:
            raise ValueError(f"{self.name} 缺少输入: {', '.join(missing)}")

        parsed, response, _ = self.ask(payload)
        relations = parsed.get("relations") or []
        if not relations:
            raise ValueError("RelationExtractorAgent 没有提出任何关系")
        for item in relations:
            absent = [key for key in REQUIRED if key not in item]
            if absent:
                raise ValueError(f"关系提案缺少字段: {', '.join(absent)}")

        trace = self.emit(
            started=started,
            payload=payload,
            decision=f"提出 {len(relations)} 条关系候选",
            output=parsed,
            response=response,
        )
        return AgentResult(
            ok=True,
            summary=f"提出 {len(relations)} 条关系",
            artifacts={"relations": relations},
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )
