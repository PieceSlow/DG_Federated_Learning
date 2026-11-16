"""AlignerAgent：在本地候选上选择字段映射（LLM 只做判断，不做数据搬运）。"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED = ("target_table", "target_field", "source_table", "source_field")


class AlignerAgent(Agent):
    name = "AlignerAgent"
    role = "字段对齐"
    goal = "从本地候选映射中为目标字段选出最合理的源字段"
    inputs = ("candidate_view",)
    outputs = ("mappings",)
    tools = ()
    prompt_id = "aligner"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        if "candidate_view" not in payload:
            raise ValueError("AlignerAgent 缺少输入: candidate_view")

        parsed, response, _ = self.ask(payload)
        mappings = parsed.get("mappings") or []
        if not mappings:
            raise ValueError("AlignerAgent 没有给出任何映射")
        for item in mappings:
            missing = [key for key in REQUIRED if not item.get(key)]
            if missing:
                raise ValueError(f"映射提案缺少字段: {', '.join(missing)}")

        trace = self.emit(
            started=started,
            payload=payload,
            decision=f"提出 {len(mappings)} 条字段映射",
            output=parsed,
            response=response,
        )
        return AgentResult(
            ok=True,
            summary=f"提出 {len(mappings)} 条映射",
            artifacts={"mappings": mappings},
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )
